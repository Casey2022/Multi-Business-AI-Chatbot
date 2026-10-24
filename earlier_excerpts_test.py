#!/usr/bin/env python3
"""earlier_excerpts_test.py: an earlier answer's excerpts are shown again.

Run it:  python3 earlier_excerpts_test.py

Each turn searched the documents for its own message only, so one turn
later the text behind an answer was gone, and the model took its own true
answers back. qa_eval 2026-10-24, 5 conversations in 20: "Correction:
disregard the drain cleaning, leak, and after-hours prices I listed
earlier" (Bob's; all in the documents), "I shouldn't have mentioned a $15
delivery fee" (Sunrise; it's there). The bakery conversation, 10 runs: 3
retractions before, none after (live model, qa_eval's no_retraction).

Now get_llm_reply also shows the excerpts for the customer's last two
questions, marked as such, with a note that those replies stand. Held here
offline: a first message's prompt is unchanged, which questions are
searched, no excerpt twice, the cap, and a search that fails.

No API calls: the model and the search are faked.
"""

import types

import booking_state_test as helpers    # stubs the integrations llm imports

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


DOCS = {
    "do you have gluten free cakes?": [
        ("Gluten-free vanilla and chocolate sponge are available to order ahead.", 0.2)],
    "how much is delivery?": [
        ("Delivery within 5 miles on orders over $50. Cakes: $15 delivery fee.", 0.2)],
    "do you use organic flour?": [],
    "can you put a photo on a cake?": [
        ("Custom designs are $20 to $60 extra.", 0.3),
        ("Delivery within 5 miles on orders over $50. Cakes: $15 delivery fee.", 0.4)],
    "lots": [(f"Excerpt number {i}.", 0.3) for i in range(9)],
}


def chat(*questions):
    history = []
    for q in questions:
        history += [{"role": "user", "content": q},
                    {"role": "assistant", "content": f"(answer to {q})"}]
    return history


def main():
    import llm
    from config import load_config
    cfg = load_config("config/sunrise_bakery_and_cafe.yaml")
    searched, sent = [], []

    def fake_retrieve(query, config):
        searched.append(query)
        if query == "the search is down":
            raise llm.RetrievalUnavailable("down")
        return list(DOCS.get(query, []))

    text = types.SimpleNamespace(type="text", text="Delivery is $15 for cakes.")

    def fake_create(**k):
        sent.append(k["system"])
        return types.SimpleNamespace(content=[text], stop_reason="end_turn",
                                     usage=types.SimpleNamespace(input_tokens=0, output_tokens=0))

    real = (llm._create, llm.retrieve)
    llm._create, llm.retrieve = fake_create, fake_retrieve
    try:
        heading("a first message: as before")
        llm.get_llm_reply("do you have gluten free cakes?", [], cfg)
        check("one search, for the message", searched == ["do you have gluten free cakes?"],
              searched)
        check("no earlier excerpts, no note", "for an earlier question" not in sent[-1]
              and llm.EARLIER_NOTE not in sent[-1])

        heading("the second message: the first one's excerpts come back")
        searched.clear()
        llm.get_llm_reply("how much is delivery?", chat("do you have gluten free cakes?"), cfg)
        check("searched for this message, then the earlier question",
              searched == ["how much is delivery?", "do you have gluten free cakes?"], searched)
        prompt = sent[-1]
        check("the earlier answer's excerpt is in the prompt, marked as earlier",
              "[from business documents, for an earlier question in this chat]\n"
              "Gluten-free vanilla and chocolate sponge" in prompt)
        check("…after this message's own excerpts",
              "Gluten-free vanilla" in prompt
              and prompt.index("Cakes: $15 delivery fee") < prompt.index("Gluten-free vanilla"))
        check("…with the note that those replies stand", llm.EARLIER_NOTE in prompt)

        heading("which questions, and how many excerpts")
        searched.clear()
        llm.get_llm_reply("can you put a photo on a cake?",
                          chat("do you have gluten free cakes?", "how much is delivery?",
                               "do you use organic flour?"), cfg)
        check("the last two earlier questions only, most recent first",
              searched == ["can you put a photo on a cake?", "do you use organic flour?",
                           "how much is delivery?"], searched)
        check("an excerpt already shown for this message isn't shown twice",
              sent[-1].count("Cakes: $15 delivery fee") == 1, sent[-1].count("Cakes: $15"))
        check("the oldest question's excerpts are left out",
              "Gluten-free vanilla" not in sent[-1])
        llm.get_llm_reply("anything else?", chat("lots"), cfg)
        check(f"at most {llm.EARLIER_EXCERPTS} earlier excerpts",
              sent[-1].count("for an earlier question in this chat]") == llm.EARLIER_EXCERPTS,
              sent[-1].count("for an earlier question in this chat]"))

        heading("when the search for an earlier question fails")
        searched.clear()
        got = llm.get_llm_reply("how much is delivery?", chat("the search is down"), cfg)
        check("the answer still comes, from this message's excerpts",
              got.startswith("Delivery is $15") and "Cakes: $15 delivery fee" in sent[-1], got)
        check("…with no note about earlier excerpts", llm.EARLIER_NOTE not in sent[-1])

        heading("the notes the customer never typed don't count as questions")
        searched.clear()
        llm.get_llm_reply("how much is delivery?",
                          [{"role": "assistant", "content": "Hi! How can I help?"}], cfg)
        check("no earlier question, no earlier search", searched == ["how much is delivery?"],
              searched)
    finally:
        llm._create, llm.retrieve = real

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
