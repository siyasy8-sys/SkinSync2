from app.resolution.relations import RelationCandidate, candidate_parents, find_relations

NAMES = {
    "sodium hyaluronate",
    "hyaluronic acid",
    "tocopheryl acetate",
    "tocopherol",
    "retinyl palmitate",
    "retinol",
    "ascorbyl palmitate",
    "ascorbic acid",
    "sodium salicylate",
    "zinc oxide",
    "hydrolyzed sodium hyaluronate",
    "glyceryl stearate",
}


def test_finds_salt_and_ester_links_only_when_parent_exists() -> None:
    assert set(find_relations(NAMES)) == {
        RelationCandidate("sodium hyaluronate", "hyaluronic acid", "salt_of"),
        RelationCandidate("tocopheryl acetate", "tocopherol", "ester_of"),
        RelationCandidate("retinyl palmitate", "retinol", "ester_of"),
        RelationCandidate("ascorbyl palmitate", "ascorbic acid", "ester_of"),
    }  # sodium salicylate: no "salicylic acid" in NAMES; glyceryl stearate: no "glycerol"


def test_non_salts_and_modified_forms_are_not_linked() -> None:
    assert candidate_parents("zinc oxide") == []
    assert candidate_parents("hydrolyzed sodium hyaluronate") == []
