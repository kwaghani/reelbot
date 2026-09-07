from __future__ import annotations

import sys
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

from retrieval import (  # noqa: E402
    QuerySlots,
    RetrievalResult,
    answer_denies_available_evidence,
    answer_question_structured,
    deterministic_query_slots,
    hybrid_rank_items,
    is_broad_plan_query,
    is_library_overview_query,
    keep_relevant_items,
    polish_answer_presentation,
    retrieve_for_query,
    should_default_location,
)
from content_quality import answerable_content, sanitize_transcript  # noqa: E402
from pipeline import structure_message_content  # noqa: E402
from worker import build_stored_content, fallback_folders  # noqa: E402


class QueryDecisionTests(unittest.TestCase):
    def test_chat_answers_restore_basic_capitalization(self) -> None:
        answer = polish_answer_presentation(
            "outdoors\n- hike around la\n\nfood\n- brunch in los angeles"
        )
        self.assertEqual(
            answer,
            "Outdoors\n• Hike around LA\n\nFood\n• Brunch in Los Angeles",
        )
        self.assertEqual(polish_answer_presentation("La Brea bakery"), "La Brea bakery")
        self.assertEqual(
            polish_answer_presentation("this starts correctly. so does this! and this one? yes."),
            "This starts correctly. So does this! And this one? Yes.",
        )
        self.assertEqual(
            polish_answer_presentation(
                "try point dume in malibu. the hollywood sign is another classic la option."
            ),
            "Try Point Dume in Malibu. The Hollywood Sign is another classic LA option.",
        )

    def test_sports_folder_uses_specific_sport_as_subfolder(self) -> None:
        self.assertEqual(
            fallback_folders({"content_type": "sports", "title": "Tennis serve drills"}),
            ("Sports", "Tennis"),
        )

    def test_pickup_line_request_is_not_library_meta(self) -> None:
        slots = QuerySlots.from_raw(
            {"intent": "meta", "cuisine_or_tags": ["pickup lines"]},
            "Do you have any pickup lines for me?",
        )
        self.assertEqual(slots.intent, "discovery")

    def test_explicit_saved_overview_is_meta(self) -> None:
        self.assertEqual(deterministic_query_slots("what do I have saved?").intent, "meta")

    def test_topic_filtered_saved_request_is_not_meta(self) -> None:
        for query in [
            "what do I have saved about workouts?",
            "list my saved items about football",
            "show me all I saved about recipes",
        ]:
            with self.subTest(query=query):
                self.assertFalse(is_library_overview_query(query))
                self.assertEqual(deterministic_query_slots(query).intent, "discovery")

    def test_named_lookup_wins_over_model_discovery_guess(self) -> None:
        slots = QuerySlots.from_raw(
            {"intent": "discovery", "location": "Munich"},
            "Ornella is in Munich?",
        )
        self.assertEqual(slots.intent, "lookup")
        self.assertEqual(slots.target_place, "Ornella")

    def test_funny_query_expands_search_synonyms(self) -> None:
        slots = deterministic_query_slots("show me something funny")
        self.assertIn("humor", slots.cuisine_or_tags or [])
        self.assertIn("comedy", slots.cuisine_or_tags or [])

    def test_only_place_queries_default_to_a_city(self) -> None:
        self.assertTrue(should_default_location(QuerySlots("discovery", category="dining")))
        self.assertFalse(
            should_default_location(
                QuerySlots("discovery", cuisine_or_tags=["pickup lines", "dating"])
            )
        )

    def test_weekend_plan_is_cross_category(self) -> None:
        self.assertTrue(
            is_broad_plan_query(
                "Things to do in LA over the weekend based on what I've sent you"
            )
        )
        self.assertFalse(is_broad_plan_query("Where should I eat this weekend?"))

    @patch("retrieval.embed_query", return_value=[0.1, 0.2])
    @patch("retrieval.count_filtered_items", return_value=2)
    @patch("retrieval.dominant_city", return_value="Los Angeles")
    @patch("retrieval.count_group_items", return_value=2)
    @patch("retrieval.connect")
    @patch("retrieval.search_items")
    def test_weekend_plan_retrieves_food_and_activities(
        self,
        search_mock,
        _connect_mock,
        _count_mock,
        _city_mock,
        _filtered_count_mock,
        _embed_mock,
    ) -> None:
        search_mock.return_value = [
            {
                "place_name": "5 Most Romantic Hikes in Los Angeles",
                "list_name": "Outdoors",
                "location_text": "Los Angeles, California",
                "tags": ["hiking", "point dume"],
                "distance": 0.40,
            },
            {
                "place_name": "The L.A. Cafe",
                "list_name": "Restaurants",
                "location_text": "Los Angeles, California",
                "tags": ["breakfast", "dining"],
                "distance": 0.72,
            },
        ]
        query = "Things to do in LA over the weekend based on what I've sent you"
        result = retrieve_for_query(
            "group",
            query,
            slots=QuerySlots("discovery", location="Los Angeles", category="attraction"),
        )

        self.assertTrue(result.broad_plan)
        self.assertEqual(
            {item["place_name"] for item in result.items},
            {"5 Most Romantic Hikes in Los Angeles", "The L.A. Cafe"},
        )
        self.assertIsNone(search_mock.call_args.kwargs["category"])
        self.assertEqual(search_mock.call_args.kwargs["location"], "Los Angeles")

    def test_source_backed_answer_cannot_claim_there_are_no_saves(self) -> None:
        self.assertTrue(
            answer_denies_available_evidence(
                "I don't actually have any saved items from you about LA yet."
            )
        )
        self.assertFalse(
            answer_denies_available_evidence(
                "You have a romantic hikes reel and The L.A. Cafe saved in LA."
            )
        )

    @patch("retrieval.compose_general_answer")
    @patch(
        "retrieval.compose_llm_answer",
        return_value={"answer": "Use the hike and cafe reels.", "used": [1, 2]},
    )
    @patch("retrieval.retrieve_for_query")
    @patch(
        "retrieval.route_message",
        return_value={"mode": "general", "reply": None, "question": "Plan an LA weekend"},
    )
    @patch("retrieval.folder_overview", return_value=(["Outdoors (1)"], ["LA hikes"]))
    def test_explicit_saved_request_cannot_be_routed_to_general_knowledge(
        self,
        _overview_mock,
        _route_mock,
        retrieve_mock,
        _compose_mock,
        general_mock,
    ) -> None:
        query = "Plan my weekend based on what I've sent you"
        retrieve_mock.return_value = RetrievalResult(
            query,
            QuerySlots("discovery"),
            [
                {"place_name": "LA Hikes", "source_url": "https://example.com/hike"},
                {"place_name": "LA Cafe", "source_url": "https://example.com/cafe"},
            ],
            broad_plan=True,
        )

        answer = answer_question_structured("group", query)

        general_mock.assert_not_called()
        self.assertEqual(retrieve_mock.call_args.args[1], query)
        self.assertEqual(len(answer["sources"]), 2)

    @patch("retrieval.compose_general_answer", return_value="Three pickup lines")
    @patch(
        "retrieval.route_message",
        return_value={"mode": "general", "reply": None, "question": "Give me pickup lines"},
    )
    @patch("retrieval.folder_overview", return_value=(["Workouts (2)"], ["Chest workout"]))
    @patch("retrieval.retrieve_for_query")
    def test_general_questions_do_not_force_irrelevant_retrieval(
        self,
        retrieve_mock,
        _overview_mock,
        _route_mock,
        _general_mock,
    ) -> None:
        result = answer_question_structured("group", "Do you have pickup lines?")
        self.assertEqual(result, {"answer": "Three pickup lines", "sources": []})
        retrieve_mock.assert_not_called()


class FolderDecisionTests(unittest.TestCase):
    def test_couple_content_is_relationships_not_comedy(self) -> None:
        folder, subfolder = fallback_folders(
            {
                "content_type": "other",
                "category": "Couple Moment",
                "title": "When your boyfriend says he does not want to go",
                "tags": ["couple", "relationship", "humor", "relatable"],
            }
        )
        self.assertEqual(folder, "Relationships & Dating")
        self.assertEqual(subfolder, "Couple Stuff")

    def test_pickup_lines_get_a_relational_subfolder(self) -> None:
        folder, subfolder = fallback_folders(
            {
                "content_type": "advice",
                "category": "Pickup Lines",
                "title": "Pickup lines that actually work",
                "tags": ["pickup lines", "flirting", "dating"],
            }
        )
        self.assertEqual((folder, subfolder), ("Relationships & Dating", "Pickup Lines"))

    def test_hikes_are_outdoors_not_generic_travel(self) -> None:
        folder, subfolder = fallback_folders(
            {
                "content_type": "travel",
                "category": "Hiking Guide",
                "title": "Romantic hikes in Los Angeles",
                "location_text": "Los Angeles",
                "tags": ["hiking", "trail", "nature", "outdoor date"],
            }
        )
        self.assertEqual((folder, subfolder), ("Outdoors", "Los Angeles"))

    def test_folder_keywords_do_not_match_inside_unrelated_words(self) -> None:
        self.assertEqual(
            fallback_folders(
                {
                    "content_type": "advice",
                    "category": "Career Advice",
                    "title": "Career advice",
                }
            )[0],
            "Money & Career",
        )
        self.assertNotEqual(
            fallback_folders(
                {
                    "content_type": "other",
                    "category": "Appetizer Ideas",
                    "title": "Easy appetizers",
                }
            )[0],
            "Tech & Gadgets",
        )

    def test_structured_details_are_stored_before_raw_content(self) -> None:
        content = build_stored_content(
            {
                "summary": "Three playful opening lines.",
                "key_details": ["Use a low-pressure compliment.", "Ask a fun question."],
                "caption": "full caption",
            }
        )
        self.assertLess(content.index("Summary:"), content.index("Original reel content:"))
        self.assertIn("Ask a fun question.", content)


class ContentQualityTests(unittest.TestCase):
    def test_repeated_background_lyrics_are_removed(self) -> None:
        text = (
            "Incline dumbbell press\n"
            "And what you do now, every other day, I'll be watching you all "
            "All I show you what it feels like now I'm on the outside All I we did "
            "everything right now I'm on the outside All I show you what it feels "
            "like now I'm on the outside All I we did everything right now I'm on the outside\n"
            "Flat dumbbell press"
        )
        cleaned = sanitize_transcript(text)
        self.assertNotIn("every other day", cleaned)
        self.assertIn("Incline dumbbell press", cleaned)
        self.assertIn("Flat dumbbell press", cleaned)

    def test_useful_speech_survives_lyrics_in_a_single_line(self) -> None:
        text = (
            "Incline dumbbell press, three sets. "
            + "I am on the outside and I am looking in " * 4
            + "Finish with cable flyes."
        )
        cleaned = sanitize_transcript(text)
        self.assertIn("Incline dumbbell press", cleaned)
        self.assertIn("Finish with cable flyes", cleaned)
        self.assertNotIn("I am on the outside", cleaned)

    def test_answers_prefer_structured_facts_over_raw_content(self) -> None:
        content = (
            "Summary: A chest workout.\n\n"
            "Key details:\n- Incline dumbbell press\n\n"
            "Original reel content:\nnoisy song lyric"
        )
        self.assertNotIn("noisy song lyric", answerable_content(content))


class RetrievalQualityTests(unittest.TestCase):
    def test_hybrid_ranking_beats_popular_but_unrelated_item(self) -> None:
        ranked = hybrid_rank_items(
            "chest workout",
            [
                {
                    "place_name": "Popular Pizza",
                    "category": "Restaurant",
                    "tags": ["pizza"],
                    "distance": 0.20,
                    "save_count": 20,
                },
                {
                    "place_name": "Whole Chest Workout",
                    "category": "Chest Workout",
                    "tags": ["chest", "workout"],
                    "distance": 0.35,
                    "save_count": 1,
                },
            ],
        )
        self.assertEqual(ranked[0]["place_name"], "Whole Chest Workout")

    def test_relevance_gate_rejects_unrelated_semantic_fallback(self) -> None:
        ranked = hybrid_rank_items(
            "pickup lines",
            [{"place_name": "Pizza Guide", "tags": ["pizza"], "distance": 0.78}],
        )
        self.assertEqual(keep_relevant_items(ranked, filtered=False), [])

    def test_structure_message_adds_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (80, 60), "red").save(root / "thumbnail.jpg")
            with patch.dict(environ, {"REELBOT_ENABLE_VISION": "1"}):
                content = structure_message_content(root, "extract this")
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[-1], {"type": "text", "text": "extract this"})


if __name__ == "__main__":
    unittest.main()
