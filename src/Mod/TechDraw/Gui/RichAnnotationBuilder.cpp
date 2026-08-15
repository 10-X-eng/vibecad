/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 ***************************************************************************/

#include "RichAnnotationBuilder.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <string_view>

#include <QCryptographicHash>
#include <QFont>
#include <QRegularExpression>
#include <QTextBlock>
#include <QTextBlockFormat>
#include <QTextDocument>
#include <QTextFragment>
#include <QTextFrame>
#include <QUrl>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawRichAnno.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/LineGroup.h>

#include "PreferencesGui.h"
#include "ViewProviderRichAnno.h"


namespace
{

constexpr std::size_t MaximumPlainInputBytes = 8 * 1024;
constexpr std::size_t MaximumHtmlInputBytes = 32 * 1024;
constexpr std::size_t MaximumCanonicalHtmlBytes = 256 * 1024;
constexpr std::size_t MaximumPlainCharacters = 8 * 1024;
constexpr std::size_t MaximumBlocks = 256;
constexpr std::size_t MaximumFragments = 2048;
constexpr std::size_t MaximumLinks = 128;
constexpr std::size_t MaximumLabelBytes = 512;
constexpr double MaximumCoordinateMm = 1.0e6;
constexpr double MaximumWidthMm = 1.0e6;
constexpr double MaximumLineWidthMm = 100.0;

constexpr auto UnsafeHtmlTokens = std::to_array<std::string_view>({
    "<script",
    "<style",
    "<link",
    "<meta",
    "<base",
    "<img",
    "<object",
    "<embed",
    "<iframe",
    "<form",
    "<input",
    "<button",
    "<textarea",
    "<select",
    "<svg",
    "<video",
    "<audio",
    "<source",
    "<track",
    "<picture",
    "<canvas",
    "<math",
    "<frame",
    "<!doctype",
    "<!entity",
    "srcdoc=",
    "javascript:",
    "url(",
    "@import",
});

std::string sha256(const QByteArray& value)
{
    return QCryptographicHash::hash(value, QCryptographicHash::Sha256)
        .toHex()
        .toStdString();
}

QString elidedPreview(const QString& text)
{
    QString simplified = text;
    simplified.replace(QChar::ParagraphSeparator, QLatin1Char('\n'));
    simplified.replace(QChar::LineSeparator, QLatin1Char('\n'));
    constexpr qsizetype MaximumPreviewCharacters = 160;
    if (simplified.size() <= MaximumPreviewCharacters) {
        return simplified;
    }
    return simplified.left(MaximumPreviewCharacters - 1) + QChar(0x2026);
}

void requireSafeProviderHtml(const QString& html)
{
    const QString lower = html.toLower();
    for (const auto token : UnsafeHtmlTokens) {
        if (lower.contains(QString::fromLatin1(token.data(), token.size()))) {
            throw Base::ValueError(
                "Rich annotation HTML may not contain resources, active content, "
                "event handlers, media, stylesheets, forms, SVG, or CSS URLs");
        }
    }
    static const QRegularExpression eventHandlerAttribute(
        QStringLiteral(R"(<[^>]*\bon[a-z][a-z0-9_-]*\s*=)"));
    if (eventHandlerAttribute.match(lower).hasMatch()) {
        throw Base::ValueError(
            "Rich annotation HTML may not contain event-handler attributes");
    }
}

TechDrawGui::DrawingRichAnnotationContent inspectDocument(
    QTextDocument& document,
    std::string inputKind)
{
    const QString plainText = document.toPlainText();
    if (static_cast<std::size_t>(plainText.size()) > MaximumPlainCharacters) {
        throw Base::ValueError(
            "Rich annotation text exceeds 8192 Unicode characters");
    }

    std::size_t blockCount = 0;
    std::size_t fragmentCount = 0;
    std::size_t linkCount = 0;
    bool hasRichFormatting = false;
    for (QTextBlock block = document.begin(); block.isValid(); block = block.next()) {
        ++blockCount;
        if (blockCount > MaximumBlocks) {
            throw Base::ValueError("Rich annotation content exceeds 256 text blocks");
        }
        const QTextBlockFormat blockFormat = block.blockFormat();
        const Qt::Alignment alignment = blockFormat.alignment();
        if (block.textList() != nullptr
            || blockFormat.indent() != 0
            || alignment.testFlag(Qt::AlignRight)
            || alignment.testFlag(Qt::AlignHCenter)
            || alignment.testFlag(Qt::AlignJustify)) {
            hasRichFormatting = true;
        }
        for (auto iterator = block.begin(); !iterator.atEnd(); ++iterator) {
            const QTextFragment fragment = iterator.fragment();
            if (!fragment.isValid()) {
                continue;
            }
            ++fragmentCount;
            if (fragmentCount > MaximumFragments) {
                throw Base::ValueError(
                    "Rich annotation content exceeds 2048 formatted fragments");
            }
            const QTextCharFormat format = fragment.charFormat();
            if (format.isImageFormat()) {
                throw Base::ValueError(
                    "Rich annotation content may not contain embedded or linked images");
            }
            if (format.isAnchor()) {
                ++linkCount;
                if (linkCount > MaximumLinks) {
                    throw Base::ValueError("Rich annotation content exceeds 128 links");
                }
                const QString href = format.anchorHref().trimmed();
                if (!href.isEmpty() && !href.startsWith(QLatin1Char('#'))) {
                    const QString scheme = QUrl(href).scheme().toLower();
                    if (scheme != QLatin1String("http")
                        && scheme != QLatin1String("https")
                        && scheme != QLatin1String("mailto")) {
                        throw Base::ValueError(
                            "Rich annotation links must use http, https, mailto, "
                            "or an in-document fragment");
                    }
                }
                hasRichFormatting = true;
            }
            if (format.fontWeight() != QFont::Normal
                || format.fontItalic()
                || format.fontUnderline()
                || format.fontStrikeOut()
                || format.foreground().style() != Qt::NoBrush
                || format.background().style() != Qt::NoBrush
                || format.verticalAlignment()
                    != QTextCharFormat::AlignNormal) {
                hasRichFormatting = true;
            }
        }
    }

    const QByteArray canonicalHtml = document.toHtml().toUtf8();
    if (static_cast<std::size_t>(canonicalHtml.size()) > MaximumCanonicalHtmlBytes) {
        throw Base::ValueError(
            "Rich annotation canonical HTML exceeds the 262144-byte limit");
    }
    const QByteArray plainUtf8 = plainText.toUtf8();
    return {
        std::move(inputKind),
        canonicalHtml.toStdString(),
        sha256(canonicalHtml),
        sha256(plainUtf8),
        elidedPreview(plainText).toUtf8().toStdString(),
        static_cast<std::size_t>(plainText.size()),
        blockCount,
        fragmentCount,
        linkCount,
        hasRichFormatting,
    };
}

TechDrawGui::DrawingRichAnnotationContent normalizeContent(
    TechDrawGui::DrawingRichAnnotationContentKind kind,
    const std::string& content)
{
    const std::size_t maximumBytes =
        kind == TechDrawGui::DrawingRichAnnotationContentKind::PlainText
        ? MaximumPlainInputBytes
        : MaximumHtmlInputBytes;
    if (content.empty() && kind != TechDrawGui::DrawingRichAnnotationContentKind::HumanEditorHtml) {
        throw Base::ValueError("A Native rich annotation requires non-empty content");
    }
    if (content.size() > maximumBytes) {
        throw Base::ValueError(
            kind == TechDrawGui::DrawingRichAnnotationContentKind::PlainText
                ? "Rich annotation plain text exceeds 8192 UTF-8 bytes"
                : "Rich annotation HTML exceeds 32768 UTF-8 bytes");
    }
    const QString unicode = QString::fromUtf8(content.data(), content.size());
    if (unicode.toUtf8().toStdString() != content) {
        throw Base::ValueError("Rich annotation content is not valid UTF-8");
    }
    if (kind != TechDrawGui::DrawingRichAnnotationContentKind::HumanEditorHtml
        && unicode.trimmed().isEmpty()) {
        throw Base::ValueError(
            "A Native rich annotation requires visible non-whitespace content");
    }
    if (kind == TechDrawGui::DrawingRichAnnotationContentKind::SafeHtml) {
        requireSafeProviderHtml(unicode);
    }
    QTextDocument document;
    if (kind == TechDrawGui::DrawingRichAnnotationContentKind::PlainText) {
        document.setPlainText(unicode);
    }
    else {
        document.setHtml(unicode);
    }
    auto result = inspectDocument(
        document,
        kind == TechDrawGui::DrawingRichAnnotationContentKind::PlainText
            ? "plain_text"
            : kind == TechDrawGui::DrawingRichAnnotationContentKind::SafeHtml
            ? "safe_html"
            : "human_editor_html");
    if (kind != TechDrawGui::DrawingRichAnnotationContentKind::HumanEditorHtml
        && QString::fromUtf8(result.plainTextPreview).trimmed().isEmpty()) {
        throw Base::ValueError("A Native rich annotation has no visible text");
    }
    if (kind == TechDrawGui::DrawingRichAnnotationContentKind::PlainText) {
        result.hasRichFormatting = false;
    }
    return result;
}

void requireLiveTargets(TechDraw::DrawPage* page, TechDraw::DrawView* owner)
{
    if (!page || !page->getDocument()) {
        throw Base::ValueError("A rich annotation requires a live Drawing page");
    }
    const auto& pageViews = page->Views.getValues();
    if (owner
        && (owner->getDocument() != page->getDocument()
            || owner->findParentPage() != page
            || std::ranges::find(pageViews, owner) == pageViews.end())) {
        throw Base::ValueError(
            "A rich annotation owner must be a live view on the exact Drawing page");
    }
}

void requireStyle(
    double xMm,
    double yMm,
    double maximumWidthMm,
    const TechDrawGui::DrawingRichAnnotationFrameStyle& frame)
{
    if (!std::isfinite(xMm) || !std::isfinite(yMm)
        || std::abs(xMm) > MaximumCoordinateMm
        || std::abs(yMm) > MaximumCoordinateMm) {
        throw Base::ValueError(
            "Rich annotation coordinates must be finite and within 1000000 mm");
    }
    if (!std::isfinite(maximumWidthMm)
        || (maximumWidthMm != -1.0
            && (maximumWidthMm <= 0.0 || maximumWidthMm > MaximumWidthMm))) {
        throw Base::ValueError(
            "Rich annotation width must be -1 for automatic or greater than 0 "
            "through 1000000 mm");
    }
    if (!std::isfinite(frame.lineWidthMm) || frame.lineWidthMm < 0.0
        || frame.lineWidthMm > MaximumLineWidthMm) {
        throw Base::ValueError(
            "Rich annotation frame line width must be from 0 through 100 mm");
    }
    if (frame.lineStyle < 0 || frame.lineStyle > 5) {
        throw Base::ValueError(
            "Rich annotation frame style must be NoLine, Continuous, Dash, Dot, "
            "DashDot, or DashDotDot");
    }
    if (!std::isfinite(frame.lineColor.r)
        || !std::isfinite(frame.lineColor.g)
        || !std::isfinite(frame.lineColor.b)
        || frame.lineColor.r < 0.0F || frame.lineColor.r > 1.0F
        || frame.lineColor.g < 0.0F || frame.lineColor.g > 1.0F
        || frame.lineColor.b < 0.0F || frame.lineColor.b > 1.0F) {
        throw Base::ValueError(
            "Rich annotation frame color channels must be from 0 through 1");
    }
}

TechDrawGui::ViewProviderRichAnno* richAnnotationProvider(
    TechDraw::DrawRichAnno* annotation)
{
    auto* provider = Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(annotation)
        : nullptr;
    auto* result = dynamic_cast<TechDrawGui::ViewProviderRichAnno*>(provider);
    if (!result) {
        throw Base::RuntimeError(
            "The rich annotation has no compatible graphical provider");
    }
    return result;
}

}  // namespace

TechDrawGui::DrawingRichAnnotationDefaults
TechDrawGui::drawingRichAnnotationDefaults()
{
    return {
        -1.0,
        {
            false,
            TechDraw::LineGroup::getDefaultWidth("Graphic"),
            1,
            PreferencesGui::leaderColor(),
        },
    };
}

TechDrawGui::DrawingRichAnnotationContent
TechDrawGui::inspectDrawingRichAnnotationContent(const std::string& storedHtml)
{
    if (storedHtml.size() > MaximumCanonicalHtmlBytes) {
        throw Base::ValueError(
            "Stored rich annotation HTML exceeds the 262144-byte inspection limit");
    }
    QTextDocument document;
    document.setHtml(QString::fromUtf8(storedHtml.data(), storedHtml.size()));
    auto result = inspectDocument(document, "stored_html");
    result.canonicalHtml = storedHtml;
    result.storedHtmlSha256 = sha256(
        QByteArray(storedHtml.data(), static_cast<qsizetype>(storedHtml.size())));
    return result;
}

TechDrawGui::DrawingRichAnnotationPlan
TechDrawGui::validateDrawingRichAnnotation(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    DrawingRichAnnotationContentKind contentKind,
    const std::string& content,
    const std::string& preferredLabel,
    double xMm,
    double yMm,
    double maximumWidthMm,
    const DrawingRichAnnotationFrameStyle& frame)
{
    requireLiveTargets(page, owner);
    requireStyle(xMm, yMm, maximumWidthMm, frame);
    if (preferredLabel.empty() || preferredLabel.size() > MaximumLabelBytes) {
        throw Base::ValueError(
            "A rich annotation label requires 1 to 512 UTF-8 bytes");
    }
    const std::string objectBaseName{"RichTextAnnotation"};
    const std::string objectName =
        page->getDocument()->getUniqueObjectName(objectBaseName.c_str());
    const std::string suffix = objectName.rfind(objectBaseName, 0) == 0
        ? objectName.substr(objectBaseName.size())
        : objectName;
    return {
        page,
        owner,
        objectName,
        preferredLabel + suffix,
        normalizeContent(contentKind, content),
        xMm,
        yMm,
        maximumWidthMm,
        frame,
    };
}

TechDraw::DrawRichAnno* TechDrawGui::createDrawingRichAnnotation(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    DrawingRichAnnotationContentKind contentKind,
    const std::string& content,
    const std::string& preferredLabel,
    double xMm,
    double yMm,
    double maximumWidthMm,
    const DrawingRichAnnotationFrameStyle& frame,
    DrawingRichAnnotationPlan* appliedPlan)
{
    DrawingRichAnnotationPlan plan = validateDrawingRichAnnotation(
        page,
        owner,
        contentKind,
        content,
        preferredLabel,
        xMm,
        yMm,
        maximumWidthMm,
        frame);
    auto* document = page->getDocument();
    if (document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Rich annotation creation requires a caller-owned document transaction");
    }

    const std::string documentName =
        Base::InterpreterSingleton::strToPython(document->getName());
    const QString factory =
        QStringLiteral("App.getDocument('%1').addObject('%2', '%3')")
            .arg(
                QString::fromStdString(documentName),
                QStringLiteral("TechDraw::DrawRichAnno"),
                QString::fromStdString(plan.objectName));
    auto* object = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        factory.toUtf8(),
        TechDraw::DrawRichAnno::getClassTypeId());
    auto* annotation = dynamic_cast<TechDraw::DrawRichAnno*>(object);
    if (!annotation) {
        throw Base::RuntimeError(
            "The rich annotation factory returned an incompatible object");
    }
    if (plan.objectName != annotation->getNameInDocument()) {
        throw Base::RuntimeError(
            "The rich annotation object identity changed after validation");
    }

    const std::string pageCommand = Gui::Command::getObjectCmd(page);
    const std::string annotationCommand = Gui::Command::getObjectCmd(annotation);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.addView(%s)",
        pageCommand.c_str(),
        annotationCommand.c_str());
    if (owner) {
        const std::string ownerCommand = Gui::Command::getObjectCmd(owner);
        Gui::Command::doCommand(
            Gui::Command::Doc,
            "%s.AnnoParent = %s",
            annotationCommand.c_str(),
            ownerCommand.c_str());
    }
    annotation->AnnoText.setValue(plan.content.canonicalHtml.c_str());
    annotation->MaxWidth.setValue(plan.maximumWidthMm);
    annotation->ShowFrame.setValue(plan.frame.visible);
    annotation->X.setValue(plan.xMm);
    annotation->Y.setValue(plan.yMm);
    annotation->Label.setValue(plan.label.c_str());

    auto* provider = richAnnotationProvider(annotation);
    provider->LineWidth.setValue(plan.frame.lineWidthMm);
    provider->LineStyle.setValue(plan.frame.lineStyle);
    provider->LineColor.setValue(plan.frame.lineColor);
    provider->LineWidth.setStatus(App::Property::ReadOnly, !plan.frame.visible);
    provider->LineStyle.setStatus(App::Property::ReadOnly, !plan.frame.visible);
    provider->LineColor.setStatus(App::Property::ReadOnly, !plan.frame.visible);

    // DrawView::requestPaint deliberately ignores objects that are not yet
    // active in History. Enroll the fully configured feature before its
    // recompute and paint signal so both page-owned and view-owned annotations
    // become visible immediately inside the caller's transaction.
    document->publishProvisionalTimelineOperationBlock(annotation, {}, {});
    if (owner) {
        owner->touch();
    }
    page->touch();
    annotation->recomputeFeature();
    if (annotation->isError() || !annotation->isValid()) {
        throw Base::RuntimeError(
            "The rich annotation could not produce a valid Drawing result");
    }
    annotation->requestPaint();

    if (appliedPlan) {
        *appliedPlan = std::move(plan);
    }
    return annotation;
}
