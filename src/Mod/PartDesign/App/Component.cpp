// SPDX-License-Identifier: LGPL-2.1-or-later

#include "Component.h"

#include <algorithm>
#include <ranges>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Uuid.h>

#include "Body.h"

using namespace PartDesign;

PROPERTY_SOURCE(PartDesign::Component, App::Part)

Component::Component()
{
    Type.setValue("Component");

    Base::Uuid componentId;
    ADD_PROPERTY_TYPE(
        ComponentId,
        (componentId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of this physical Component"
    );

    Base::Uuid designId;
    ADD_PROPERTY_TYPE(
        DesignId,
        (designId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the Design which owns this Component"
    );
}

void Component::setupObject()
{
    App::Part::setupObject();
    auto* document = getDocument();
    if (!document || document->testStatus(App::Document::Restoring)) {
        return;
    }
    if (auto* timeline = App::DocumentTimeline::ensure(document)) {
        DesignId.setValue(timeline->DesignId.getValue());
    }
}

void Component::onChanged(const App::Property* property)
{
    App::Part::onChanged(property);
    if (property != &Group || !getDocument() || getDocument()->testStatus(App::Document::Restoring)) {
        return;
    }

    const std::string componentId = ComponentId.getValueStr();
    for (auto* body : getDocument()->getObjectsOfType<Body>()) {
        if (!body) {
            continue;
        }
        const bool directMember = std::ranges::find(Group.getValues(), body)
            != Group.getValues().end();
        if (directMember) {
            body->ComponentId.setValue(componentId);
            body->DesignId.setValue(DesignId.getValue());
        }
        else if (body->ComponentId.getValue() == componentId) {
            body->ComponentId.setValue("");
        }
    }
}
