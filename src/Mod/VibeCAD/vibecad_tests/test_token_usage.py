        "gpt-6-astra (transport)",
    ]
    assert graph["rows"][0]["counts"]["cached_input_tokens"] == 200
    assert graph["rows"][0]["counts"]["reasoning_output_tokens"] is None




def test_usage_graph_data_is_empty_without_actual_usage() -> None:
    from VibeCADTokenUsage import usage_graph_data


    graph = usage_graph_data({"has_usage": False})


    assert graph == {"has_usage": False, "complete": False, "rows": []}






def test_usage_graph_data_preserves_conversation_and_model_counts() -> None:
    from VibeCADTokenUsage import usage_graph_data


    summary = {
        "has_usage": True,
        "complete": False,
        "totals": {
            "input_tokens": 300,
            "cached_input_tokens": 200,
            "output_tokens": 40,
            "reasoning_output_tokens": None,
            "total_tokens": 340,
        },
        "models": {
            "gpt-6-astra": {
                "input_tokens": 300,
                "cached_input_tokens": 200,
                "output_tokens": 40,
                "reasoning_output_tokens": None,
                "total_tokens": 340,
                "model_source": "transport",
            }
        },
        "turns": [],
    }


    graph = usage_graph_data(summary)


    assert graph["has_usage"] is True
    assert graph["complete"] is False
    assert [row["label"] for row in graph["rows"]] == [
        "Conversation",
        "gpt-6-astra (transport)",
    ]
    assert graph["rows"][0]["counts"]["cached_input_tokens"] == 200
    assert graph["rows"][0]["counts"]["reasoning_output_tokens"] is None
