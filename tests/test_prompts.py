from autobugfix.prompts import load_role_instructions, writer_prompt


def test_prompts_load_role_skill_and_render_writer_prompt():
    instructions = load_role_instructions(__import__("pathlib").Path.cwd(), "writer")
    assert "Autobugfix Writer" in instructions
    assert "Task:" in writer_prompt("task", "context", "feedback")


def test_prompts_load_custom_role_skill_paths(tmp_path):
    skill = tmp_path / "custom-writer.md"
    skill.write_text("# Custom Writer\n", encoding="utf-8")
    instructions = load_role_instructions(tmp_path, "writer", skill_paths=[skill])
    assert "Custom Writer" in instructions
