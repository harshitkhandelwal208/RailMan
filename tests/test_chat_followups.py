import unittest
from unittest.mock import AsyncMock, patch

from app.services import ai_engine
from app.services.context_resolver import resolve_entities


class ChatFollowupTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_ai_response_removes_internal_sections(self):
        leaked = """ASSISTANT:
--- Relevant knowledge ---
internal note
--- End knowledge ---
SYSTEM:
hidden

Best option is the **17:36 Virar Fast**.
"""
        cleaned = ai_engine._sanitize_ai_response(leaked)
        self.assertEqual(cleaned, "Best option is the **17:36 Virar Fast**.")

    async def test_history_loader_keeps_entity_metadata(self):
        fake_history = [{"role": "user", "content": "Borivali to Churchgate", "entities": {"source": "Borivali"}}]

        with patch("app.db.chat_db.get_chat_history", new=AsyncMock(return_value=fake_history)) as mocked_get_history:
            history = await ai_engine._get_history_from_db("sess_123", "conv_123")

        self.assertEqual(history, fake_history)
        mocked_get_history.assert_awaited_once_with(
            "sess_123",
            limit=ai_engine.MAX_HISTORY,
            conversation_id="conv_123",
            include_metadata=True,
        )

    async def test_handle_query_resolves_followup_from_prior_turn_entities(self):
        prior_history = [
            {
                "role": "user",
                "content": "Churchgate to Virar at 17:30",
                "entities": {
                    "source": "Churchgate",
                    "destination": "Virar",
                    "time": "17:30",
                    "preference": "balanced",
                },
            },
            {
                "role": "assistant",
                "content": "Try the 17:36 fast.",
                "entities": {},
            },
        ]
        fake_recommendation = {
            "best": {
                "name": "Virar Fast",
                "departs": "18:00",
                "travel_minutes": 78,
                "crowd": "High",
                "wait_minutes": 30,
                "type": "fast",
            },
            "alternatives": [],
            "meta": {"trains_evaluated": 12},
        }

        with patch("app.services.ai_engine._get_history_from_db", new=AsyncMock(return_value=prior_history)), \
             patch("app.services.ai_engine._get_semantic_memory", new=AsyncMock(return_value=[])), \
             patch("app.services.ai_engine._save_turn_to_db", new=AsyncMock()), \
             patch("app.services.ai_engine.extract_entities", return_value={}), \
             patch("app.services.ai_engine.generate_with_providers", return_value=None), \
             patch("app.services.ai_engine.recommend_sync", return_value=fake_recommendation), \
             patch("app.services.ai_engine.recommend", new=AsyncMock(return_value={"error": "skip-card"})), \
             patch("app.services.ai_engine._build_knowledge_snippet", return_value=""):
            result = await ai_engine.handle_query(
                "what about 30 minutes later and least crowded instead?",
                session_id="sess_123",
                conversation_id="conv_123",
            )

        entities = result["meta"]["entities"]
        self.assertEqual(entities["source"], "Churchgate")
        self.assertEqual(entities["destination"], "Virar")
        self.assertEqual(entities["time"], "18:00")
        self.assertEqual(entities["preference"], "least_crowded")
        self.assertTrue(result["meta"]["followup_resolved"])
        self.assertEqual(result["meta"]["conversation_id"], "conv_123")

    async def test_rule_based_reply_does_not_append_knowledge_block(self):
        fake_recommendation = {
            "best": {
                "name": "Virar Fast",
                "departs": "17:36",
                "travel_minutes": 81,
                "crowd": "Extreme",
                "wait_minutes": 2,
                "type": "fast",
            },
            "alternatives": [],
            "meta": {"trains_evaluated": 142},
        }
        with patch("app.services.ai_engine.recommend_sync", return_value=fake_recommendation), \
             patch("app.services.ai_engine._build_knowledge_snippet", return_value="--- Relevant knowledge ---\nsecret\n--- End knowledge ---"):
            text = ai_engine._rule_based(
                "churchgate to virar",
                {"source": "Churchgate", "destination": "Virar", "preference": "balanced"},
            )

        self.assertIn("Virar Fast", text)
        self.assertNotIn("Relevant knowledge", text)

    def test_recommendation_style_followup_inherits_route_and_wait_preference(self):
        history = [
            {
                "role": "user",
                "content": "Best train Borivali to Churchgate 9 AM",
                "entities": {
                    "source": "Borivali",
                    "destination": "Churchgate",
                    "time": "09:00",
                    "preference": "balanced",
                },
            }
        ]

        merged = resolve_entities(
            "what would you recommend if i don't mind waiting",
            {"source": None, "destination": None, "time": None, "preference": "balanced"},
            history,
        )

        self.assertEqual(merged["source"], "Borivali")
        self.assertEqual(merged["destination"], "Churchgate")
        self.assertEqual(merged["time"], "09:00")
        self.assertEqual(merged["preference"], "least_crowded")


if __name__ == "__main__":
    unittest.main()
