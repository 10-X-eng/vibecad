from AeroRouting import RouteCandidate, choose_route


def test_routing_is_deterministic_and_explainable() -> None:
    decision = choose_route([
        RouteCandidate("openfoam", "local", qualified=True, available=True, fidelity_rank=5, estimated_wall_time_s=1000),
        RouteCandidate("fluidx3d", "kaggle", qualified=True, available=True, fidelity_rank=4, quota_fit=False),
        RouteCandidate("vlm", "local", qualified=True, available=True, fidelity_rank=2, estimated_wall_time_s=1),
    ])
    assert decision.selected.solver == "openfoam"
    assert any("selected=openfoam@local" in item for item in decision.rationale)
    assert decision.rejected[0][1] == ("provider_quota_estimate_does_not_fit",)
