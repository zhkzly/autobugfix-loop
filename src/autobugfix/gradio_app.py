from __future__ import annotations

from pathlib import Path

from autobugfix.projection import render_inspect, status_projection
from autobugfix.service import AutobugfixService


def create_app(project_root: Path | str = "."):
    import gradio as gr

    service = AutobugfixService(project_root)

    def status_text() -> str:
        return str(status_projection(service.store))

    def inspect_text(task_id: str) -> str:
        return render_inspect(__import__("autobugfix.projection", fromlist=["inspect_projection"]).inspect_projection(service.store, task_id))

    def run_task(task_id: str) -> str:
        record = service.run_task(task_id)
        return f"{record.task_id}: {record.state}"

    with gr.Blocks(title="Autobugfix Operator") as app:
        gr.Markdown("# Autobugfix Operator")
        refresh = gr.Button("Refresh")
        status = gr.Textbox(label="Status", lines=8)
        task_id = gr.Textbox(label="Task ID")
        inspect = gr.Button("Inspect")
        run = gr.Button("Run")
        output = gr.Textbox(label="Output", lines=16)
        refresh.click(status_text, outputs=status)
        inspect.click(inspect_text, inputs=task_id, outputs=output)
        run.click(run_task, inputs=task_id, outputs=output)
    return app


def launch(host: str, port: int, project_root: Path | str = ".") -> None:
    create_app(project_root).launch(server_name=host, server_port=port)
