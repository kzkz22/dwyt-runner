"""Categories (decision 19.13): after the summary the language model chooses from the user's categories by id;
older items come as jobs of kind categorize."""
from dwyt_runner import categorize
from test_runner import _run_once

CATS = [{"id": 3, "name": "Otthon, barkács, kert", "description": "Lakás, felújítás, kertészet."},
        {"id": 7, "name": "Természet és környezet", "description": "Állatok, növények, klíma."},
        {"id": 9, "name": "Tudomány és kutatás", "description": "Kutatási eredmények."}]
TRANSCRIPT = {"language": "hu", "mode": "captions", "segments": [
    {"start": 0, "end": 2, "speaker": None, "text": "Ma a kertről beszélünk."}]}


def test_the_answer_counts_only_with_listed_ids():
    assert categorize.parse('{"main": 3, "side": [7, 7, 42, 9, 3]}', CATS) == {
        "main": 3, "side": [7, 9], "suggestion": None}
    assert categorize.parse('Íme: {"main": 42, "side": ["9"], "suggestion": {"name": "  Méhészet ", '
                            '"description": "Méhek"}}', CATS) == {
        "main": 9, "side": [], "suggestion": {"name": "Méhészet", "description": "Méhek"}}
    assert categorize.parse('{"main": null, "side": []}', CATS) == {"main": None, "side": [], "suggestion": None}
    assert categorize.parse("nem tudom", CATS) is None
    assert categorize.parse('{"main": 3,', CATS) is None


def test_the_summary_carries_the_chosen_categories(cfg, central, llm):
    llm.category_answers = ['<think>hmm</think>{"main": 3, "side": [7], "suggestion": null}']
    central.add_job(30, "summarize", {"role": "public", "language": "hu", "meta": {"title": "Kerti videó"},
                                      "transcript": TRANSCRIPT, "categories": CATS})
    _run_once(cfg, central)
    res = central.results[30]
    assert res["text"] == "## Összefoglaló\nKert." and res["categories"] == {"main": 3, "side": [7], "suggestion": None}
    assert res["prompt_tokens"] == 70  # the summary's 50 and the choice's 20
    ask = llm.calls[-1]["messages"]
    assert "3: Otthon, barkács, kert – Lakás, felújítás, kertészet." in ask[1]["content"]
    assert "## Összefoglaló\nKert." in ask[1]["content"] and "Cím: Kerti videó" in ask[1]["content"]
    assert "ne kövesd" in ask[0]["content"]  # the summary is data, not instructions
    assert "categorize" in central.claims[0]["capabilities"]["kinds"]


def test_an_unreadable_choice_is_asked_once_more_and_never_costs_the_summary(cfg, central, llm):
    llm.category_answers = ["Szerintem kert.", '{"main": 9}']
    central.add_job(31, "summarize", {"role": "public", "transcript": TRANSCRIPT, "categories": CATS})
    _run_once(cfg, central)
    assert central.results[31]["categories"]["main"] == 9
    assert llm.calls[-1]["messages"][-1]["content"].startswith("Csak a JSON")
    llm.category_answers = ["nem", "megint nem"]
    central.add_job(32, "summarize", {"role": "public", "transcript": TRANSCRIPT, "categories": CATS})
    _run_once(cfg, central)
    assert "categories" not in central.results[32] and central.results[32]["text"]
    central.add_job(33, "summarize", {"role": "public", "transcript": TRANSCRIPT})  # an older central: no list
    calls = len(llm.calls)
    _run_once(cfg, central)
    assert "categories" not in central.results[33] and len(llm.calls) == calls + 1


def test_an_older_item_is_categorized_by_the_role_model(cfg, central, llm):
    central.add_job(34, "categorize", {"role": "private", "meta": {"title": "Saját felvétel"},
                                       "summary": "## Összefoglaló\nA kerti munkákról.", "categories": CATS})
    _run_once(cfg, central)
    res = central.results[34]
    assert res["categories"] == {"main": 3, "side": [], "suggestion": None}
    assert res["model"] == cfg.llm_private_model and res["prompt_version"] == 1 and res["prompt_tokens"] == 20
    llm.category_answers = ["semmi", "semmi"]
    central.add_job(35, "categorize", {"role": "public", "summary": "x", "categories": CATS})
    _run_once(cfg, central)
    assert central.failures[35]["retry"] == "never" and "kategória" in central.failures[35]["error"]
