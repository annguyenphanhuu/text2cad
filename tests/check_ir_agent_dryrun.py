"""
Dry run of the agent wiring: greeting -> IR extraction -> confirmation -> 'ok' -> build,
with save_outputs monkeypatched (no FreeCAD server needed).

    SKIP_DB=true python tests/check_ir_agent_dryrun.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SKIP_DB", "true")

PROMPT = ("Je veux un support de fixation moteur en tôle inox 304L de 4 mm. Base 160 mm de long et 50 mm de large. "
          "Un pli à 90° forme un retour vertical de 80 mm avec un trou Ø12 centré à 50 mm de la base. "
          "La partie horizontale reçoit 2 trous Ø8 à 120 mm d'entraxe, centrés, à 20 mm du bord. Rayon intérieur 4 mm.")


async def run_turn(agent, text, sid, is_edit=False):
    final = None
    steps = []
    async for upd in agent.process_request_with_progress(text, is_edit_request=is_edit, session_id=sid):
        if "final_result" in upd:
            final = upd["final_result"]
        else:
            steps.append(upd.get("step"))
    return final, steps


async def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.core.chatbot import text_to_cad_agent as agent

    captured = {}

    async def fake_save_outputs(code, requirements, base_filename="generated_cad", user_text="", session_id=None, priority=0, skip_metadata=False):
        captured.update(code=code, shape_type=requirements.shape_type, title=requirements.title, skip_metadata=skip_metadata)
        return ("fake.obj", "fake.step", "fake.pdf")

    agent.save_outputs = fake_save_outputs
    sid = "dryrun_%d" % int(time.time())

    final, steps = await run_turn(agent, PROMPT, sid)
    msg = (final or {}).get("message") or ""
    assert final and final.get("code") is None and "Reply yes/ok" in msg, final
    print("turn 1 -> confirmation shown (%d chars), steps=%s" % (len(msg), steps))
    print(msg[:700])
    state = agent._get_session_state(sid)
    assert state.get("awaiting_confirm") and state.get("ir_pending"), "confirm gate not armed"

    final, steps = await run_turn(agent, "ok", sid)
    assert final and final.get("code") and "build_and_export" in final["code"], final
    assert captured.get("skip_metadata") is True and captured.get("shape_type") == "L-bracket", captured
    assert final.get("step_path") == "fake.step"
    print("turn 2 -> built via stub script | shape_type=%s title=%s | steps=%s" % (captured["shape_type"], captured["title"], steps))
    state = agent._get_session_state(sid)
    assert state.get("latest_ir") and not state.get("awaiting_confirm")

    final, steps = await run_turn(agent, "ajoute un trou Ø6 au centre de la base", sid, is_edit=True)
    assert final and final.get("code"), final
    ir = state.get("latest_ir")
    assert any(f.get("diameter") == 6 for f in ir["features"]), ir["features"]
    print("turn 3 -> edit applied: %d features now | %s" % (len(ir["features"]), final.get("edit_summary")))
    print("DRY RUN OK")


if __name__ == "__main__":
    asyncio.run(main())
