import json
import unittest
from pathlib import Path

from parliament_mcp_india.data import DATASET, ParliamentDataset

FIXTURE = Path(__file__).parent / "fixtures" / "parliament_mcp_fixture.json"


class ParliamentDatasetTests(unittest.TestCase):
    def setUp(self):
        self.d = ParliamentDataset.load(FIXTURE)

    def test_withheld_records_never_appear(self):
        self.assertIsNone(self.d.get_member("ls-fictional-withheld"))
        self.assertNotIn("ls-fictional-withheld", {m["id"] for m in self.d.list_members()["members"]})
        self.assertIsNone(self.d.get_committee("withheld-committee"))

    def test_internal_fields_are_not_exposed(self):
        member = self.d.list_members()["members"][0]
        self.assertNotIn("permissions", member)
        self.assertNotIn("prs_image_id", member)

    def test_list_members_filters_and_paginates(self):
        page = self.d.list_members(house="Lok Sabha")
        self.assertEqual([m["id"] for m in page["members"]], ["ls-fictional-one"])
        self.assertEqual(self.d.list_members(limit=1, offset=1)["members"][0]["id"], "rs-fictional-two")
        self.assertEqual(self.d.list_members()["total"], 2)

    def test_invalid_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            self.d.list_members(house="Lok sabha ")
        with self.assertRaises(ValueError):
            self.d.list_members(limit=0)
        with self.assertRaises(ValueError):
            self.d.search_members("a")

    def test_search_members_by_name_and_constituency(self):
        self.assertEqual([m["id"] for m in self.d.search_members("fictional north")], ["ls-fictional-one"])
        self.assertEqual(len(self.d.search_members("fictional member")), 2)

    def test_get_member_includes_committee_roles(self):
        roles = self.d.get_member("rs-fictional-two")["committees"]
        self.assertEqual(roles, [{"committee_id": "fictional-standing-committee", "name": "Standing Committee on Fictional Testing", "role": "Chairperson"}])

    def test_relevant_mps_for_bill_puts_chair_first_and_separates_former_mps(self):
        result = self.d.relevant_mps_for_bill("fictional-testing-bill-2026")
        members = result["committees"][0]["members"]
        self.assertEqual(members[0]["name"], "B. Fictional Member")
        self.assertTrue(members[0]["is_chair"])
        self.assertNotIn("Unmatched Person", [m["name"] for m in members])
        self.assertEqual(result["committees"][0]["listed_but_no_longer_mps"], ["Unmatched Person"])
        self.assertIn("not whether they support it", result["reason"])

    def test_find_bill_by_citizenview_id_and_exact_title(self):
        self.assertEqual(self.d.find_bill("fictional-testing-2026")["id"], "fictional-testing-bill-2026")
        self.assertEqual(self.d.find_bill("The Fictional Testing Bill, 2026")["id"], "fictional-testing-bill-2026")

    def test_ambiguous_title_fragment_returns_nothing(self):
        self.assertIsNone(self.d.find_bill("fictional testing"))


@unittest.skipUnless(DATASET.exists(), "real dataset not built yet")
class RealDatasetTests(unittest.TestCase):
    def test_roster_sizes_are_plausible(self):
        stats = json.loads(DATASET.read_text(encoding="utf-8"))["stats"]
        self.assertTrue(500 <= stats["lok_sabha_members"] <= 550, stats)
        self.assertTrue(200 <= stats["rajya_sabha_members"] <= 250, stats)

    def test_old_committees_are_flagged(self):
        d = ParliamentDataset.load()
        old = [c["name"] for c in d.list_committees() if not c["current"]]
        self.assertTrue(any("Personal Data Protection Bill, 2019" in name for name in old), old)

    def test_every_tracked_bill_links_to_one_current_committee(self):
        d = ParliamentDataset.load()
        for bill in d.bills:
            committees = d.relevant_mps_for_bill(bill["id"])["committees"]
            self.assertEqual(len(committees), 1, bill["title"])
            self.assertTrue(committees[0]["current"], bill["title"])
            self.assertTrue(committees[0]["members"], bill["title"])

    def test_every_tracked_bill_loads(self):
        d = ParliamentDataset.load()
        self.assertTrue(d.bills)
        for bill in d.bills:
            self.assertIsNotNone(d.relevant_mps_for_bill(bill["id"]))


if __name__ == "__main__":
    unittest.main()
