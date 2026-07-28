# import os
# import unittest
# from pathlib import Path
#
# os.environ.pop("ANTHROPIC_API_KEY", None)
# os.environ["ALLOW_DEMO_FALLBACK"] = "true"
#
# from app.rag import KnowledgeBase, parse_age_range, tokenize
# from app.server import generate_chat, generate_pathway
#
#
# ROOT = Path(__file__).resolve().parent.parent
#
#
# class KnowledgeBaseTests(unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         cls.kb = KnowledgeBase(ROOT / "data" / "mosaic_resources.csv")
#
#     def test_loads_expected_seed_records(self):
#         self.assertEqual(len(self.kb.resources), 29)
#         self.assertEqual(self.kb.resources[0].id, "R001")
#
#     def test_tokenizer_drops_common_words(self):
#         self.assertEqual(tokenize("What about gaming and neurodivergence?"), ["about", "gaming", "neurodivergence"])
#
#     def test_age_range_parsing(self):
#         self.assertEqual(parse_age_range("6-14"), (6, 14))
#         self.assertEqual(parse_age_range("15-18+"), (15, 18))
#         self.assertIsNone(parse_age_range("All Ages"))
#
#     def test_retrieves_gaming_resource(self):
#         results = self.kb.search("gaming digital worlds neurodivergent learner", ages=[10], limit=3)
#         self.assertIn("gaming", results[0].title.lower())
#         self.assertIn(results[0].id, {"R004", "R011"})
#
#     def test_community_filter_returns_only_community_records(self):
#         results = self.kb.search("belonging and connection", community=True, limit=3)
#         self.assertTrue(results)
#         self.assertTrue(all(resource.is_community for resource in results))
#
#
# class DemoGenerationTests(unittest.TestCase):
#     def setUp(self):
#         self.profile = {
#             "ages": [9],
#             "interests": "gaming and building",
#             "learning_needs": "predictability and movement",
#             "leave_behind": "constant evaluation",
#             "preserve": "friendships",
#             "add": "more autonomy",
#             "values": "trust and curiosity",
#         }
#
#     def test_chat_returns_grounded_sources_without_key(self):
#         result = generate_chat({"message": "Can gaming be learning?", "profile": self.profile, "history": []})
#         self.assertEqual(result["mode"], "demo")
#         self.assertGreaterEqual(len(result["sources"]), 1)
#         self.assertRegex(result["message"], r"\[R\d{3}\]")
#
#     def test_pathway_uses_canonical_resource_records(self):
#         result = generate_pathway({
#             "profile": self.profile,
#             "history": [
#                 {
#                     "role": "user",
#                     "content": "We want to focus on creating a quieter morning rhythm.",
#                 },
#                 {
#                     "role": "assistant",
#                     "content": "A Mosaic resource suggests beginning with observation before adding structure. [R003]",
#                 },
#             ],
#         })
#         pathway = result["pathway"]
#         self.assertEqual(result["mode"], "demo")
#         self.assertIn("quieter morning rhythm", pathway["reflection"])
#         self.assertIn("beginning with observation", pathway["reflection"])
#         self.assertIn("quieter morning rhythm", pathway["rhythm"][0]["practice"])
#         self.assertGreaterEqual(len(pathway["resources"]), 2)
#         self.assertTrue(all(item["source_url"].startswith("http") for item in pathway["resources"]))
#         self.assertTrue(pathway["community"]["is_community"])
#
#
# if __name__ == "__main__":
#     unittest.main()
