// SPDX-License-Identifier: LGPL-2.1-or-later
#include <FCConfig.h>
#include "SimulationPlaybackCache.h"

#include <algorithm>
#include <limits>
#include <ios>
#include <fstream>
#include <iomanip>
#include <locale>
#include <streambuf>
#include <utility>
#include <QCryptographicHash>
#include <QDataStream>
#include <QDir>
#include <QFile>
#include <QSaveFile>
#include <QSysInfo>
#include <Base/CancellationScope.h>
#include <OndselSolver/ASMTAssembly.h>
#include <OndselSolver/ASMTPart.h>
#include <OndselSolver/ASMTJoint.h>
#include <OndselSolver/ASMTMotion.h>
#include <OndselSolver/ASMTLimit.h>
#include <OndselSolver/ASMTKinematicIJ.h>
#include <OndselSolver/ASMTForceTorque.h>
#include <OndselSolver/ASMTRefPoint.h>

namespace Assembly::detail
{
namespace
{
constexpr char magic[] = "VibeCAD-frames-1";
constexpr qint64 headerSize = sizeof(magic) - 1 + 1 + 64 + 16 + 32;

// The existing Ondsel serializer takes ofstream, but writes through ostream's
// stream buffer. Hash directly: no temporary input file or full text allocation.
class HashBuffer : public std::streambuf
{
public:
    QCryptographicHash hash {QCryptographicHash::Sha256};
protected:
    std::streamsize xsputn(const char* data, std::streamsize size) override
    {
        hash.addData(QByteArrayView(data, qsizetype(size)));
        return size;
    }
    int_type overflow(int_type value) override
    {
        if (!traits_type::eq_int_type(value, traits_type::eof())) {
            const char byte = traits_type::to_char_type(value);
            hash.addData(QByteArrayView(&byte, 1));
        }
        return traits_type::not_eof(value);
    }
};

QByteArray inputDigest(MbD::ASMTItem& item)
{
    Base::CancellationScope::check();
    HashBuffer buffer;
    std::ofstream stream;
    static_cast<std::ios&>(stream).rdbuf(&buffer);
    stream.exceptions(std::ios::badbit | std::ios::failbit);
    stream.imbue(std::locale::classic());
    stream << std::setprecision(std::numeric_limits<double>::max_digits10);
    item.storeOnLevel(stream, 0);
    stream.flush();
    return buffer.hash.result();
}

template<class T>
auto sortedInputs(const std::shared_ptr<std::vector<std::shared_ptr<T>>>& source)
{
    std::vector<std::pair<QByteArray, std::shared_ptr<T>>> entries;
    entries.reserve(source->size());
    for (const auto& item : *source) { entries.emplace_back(inputDigest(*item), item); }
    std::sort(entries.begin(), entries.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    auto result = std::make_shared<std::vector<std::shared_ptr<T>>>();
    result->reserve(entries.size());
    for (auto& entry : entries) { result->push_back(std::move(entry.second)); }
    return result;
}

QString cachePath(const std::string& directory, const std::string& key)
{
    if (key.size() != 64 || !std::all_of(key.begin(), key.end(), [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        })) {
        throw std::invalid_argument("Invalid simulation playback input digest");
    }
    return QDir(QString::fromStdString(directory)).filePath(QString::fromStdString(key) + ".frames");
}

QByteArrayView rowBytes(const std::vector<double>& row)
{
    if (row.size() > size_t(std::numeric_limits<qsizetype>::max()) / sizeof(double)) {
        throw std::length_error("Simulation playback channel exceeds addressable memory");
    }
    return {reinterpret_cast<const char*>(row.data()), qsizetype(row.size() * sizeof(double))};
}
}

std::string SimulationPlaybackCache::inputKey(
    const MbD::ASMTAssembly& assembly, const std::vector<Binding>& bindings,
    const std::string& producer)
{
    if (!assembly.times->empty() || !assembly.constraintSets->empty()) {
        throw std::invalid_argument("Playback key requires detached, unserialized-result-free solver inputs");
    }
    // Canonicalize copies only. Unordered host capture must not defeat reuse,
    // and constructing the key must not change the actual solver's input order.
    auto canonical = assembly;
    canonical.parts = std::make_shared<std::vector<std::shared_ptr<MbD::ASMTPart>>>();
    for (const auto& part : *assembly.parts) {
        auto copy = std::make_shared<MbD::ASMTPart>(*part);
        copy->refPoints = sortedInputs(part->refPoints);
        canonical.parts->push_back(std::move(copy));
    }
    canonical.parts = sortedInputs(canonical.parts);
    canonical.refPoints = sortedInputs(assembly.refPoints);
    canonical.joints = sortedInputs(assembly.joints);
    canonical.motions = sortedInputs(assembly.motions);
    canonical.limits = sortedInputs(assembly.limits);
    canonical.kinematicIJs = sortedInputs(assembly.kinematicIJs);
    canonical.forcesTorques = sortedInputs(assembly.forcesTorques);
    QCryptographicHash hash(QCryptographicHash::Sha256);
    hash.addData(inputDigest(canonical));
    hash.addData(QByteArray::fromStdString(producer));
    for (const auto& [name, offset] : bindings) {
        QByteArray binding;
        QDataStream stream(&binding, QIODevice::WriteOnly);
        stream.setVersion(QDataStream::Qt_6_0);
        stream << QByteArray::fromStdString(name);
        const auto& position = offset.getPosition();
        stream << position.x << position.y << position.z;
        for (size_t index = 0; index < 4; ++index) { stream << offset.getRotation()[index]; }
        hash.addData(binding);
    }
    return hash.result().toHex().toStdString();
}

SimulationPlaybackCache::SimulationPlaybackCache(std::string directory)
    : directory(std::move(directory))
{}

void SimulationPlaybackCache::store(
    const std::string& key, const std::vector<SimulationFrameTrack>& tracks) const
{
    const auto path = cachePath(directory, key);
    if (tracks.empty() || tracks.front().frameCount() < 2) {
        throw std::invalid_argument("Cannot cache incomplete simulation playback");
    }
    const size_t frames = tracks.front().frameCount();
    QCryptographicHash hash(QCryptographicHash::Sha256);
    for (const auto& track : tracks) {
        Base::CancellationScope::check();
        for (const auto& row : track.channels) {
            if (row.size() != frames || !std::all_of(row.begin(), row.end(), [](double value) {
                    return std::isfinite(value);
                })) {
                throw std::invalid_argument("Cannot cache invalid simulation playback channels");
            }
            hash.addData(rowBytes(row));
        }
    }
    if (!QDir().mkpath(QString::fromStdString(directory))) {
        throw std::ios_base::failure("Cannot create simulation playback cache directory");
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        throw std::ios_base::failure("Cannot write simulation playback cache");
    }
    QDataStream stream(&file);
    stream.writeRawData(magic, sizeof(magic) - 1);
    stream << quint8(QSysInfo::ByteOrder);
    stream.writeRawData(key.data(), key.size());
    stream << quint64(frames) << quint64(tracks.size());
    const auto digest = hash.result();
    stream.writeRawData(digest.data(), digest.size());
    for (const auto& track : tracks) {
        Base::CancellationScope::check();
        for (const auto& row : track.channels) {
            const auto bytes = rowBytes(row);
            if (file.write(bytes.data(), bytes.size()) != bytes.size()) {
                throw std::ios_base::failure("Incomplete simulation playback cache write");
            }
        }
    }
    Base::CancellationScope::check();
    if (stream.status() != QDataStream::Ok || !file.commit()) {
        throw std::ios_base::failure("Cannot commit simulation playback cache");
    }
}

std::optional<std::vector<SimulationFrameTrack>> SimulationPlaybackCache::load(
    const std::string& key, const std::vector<Base::Placement>& offsets) const
{
    QFile file(cachePath(directory, key));
    if (!file.open(QIODevice::ReadOnly) || file.size() < headerSize) { return {}; }
    if (file.read(sizeof(magic) - 1) != QByteArray(magic, sizeof(magic) - 1)) { return {}; }
    QDataStream stream(&file);
    quint8 byteOrder;
    stream >> byteOrder;
    if (byteOrder != QSysInfo::ByteOrder || file.read(64) != QByteArray::fromStdString(key)) {
        return {};
    }
    quint64 frames = 0, count = 0;
    stream >> frames >> count;
    const auto digest = file.read(32);
    const quint64 payloadBytes = file.size() - headerSize;
    // Validate dimensions against the actual file before allocating. No model-
    // size cap: these checks prevent overflow and allocation from a forged header.
    constexpr quint64 bytesPerSample = 6 * sizeof(double);
    if (stream.status() != QDataStream::Ok || digest.size() != 32 || frames < 2
        || count == 0 || count != offsets.size() || payloadBytes % bytesPerSample != 0
        || (payloadBytes / bytesPerSample) % count != 0
        || (payloadBytes / bytesPerSample) / count != frames
        || frames > std::vector<double>().max_size()) {
        return {};
    }
    std::vector<SimulationFrameTrack> tracks(offsets.size());
    QCryptographicHash hash(QCryptographicHash::Sha256);
    for (size_t index = 0; index < tracks.size(); ++index) {
        Base::CancellationScope::check();
        auto& track = tracks[index];
        track.offset = offsets[index];
        for (auto& row : track.channels) {
            row.resize(size_t(frames));
            const auto bytes = rowBytes(row);
            if (file.read(reinterpret_cast<char*>(row.data()), bytes.size()) != bytes.size()) {
                return {};
            }
            hash.addData(bytes);
            if (!std::all_of(row.begin(), row.end(), [](double value) { return std::isfinite(value); })) {
                return {};
            }
        }
    }
    if (!file.atEnd() || hash.result() != digest) { return {}; }
    return tracks;
}
}
