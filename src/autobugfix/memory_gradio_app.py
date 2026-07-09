from __future__ import annotations

from pathlib import Path

from autobugfix.memory.service import MemoryService


def create_app(project_root: Path | str = "."):
    import gradio as gr

    service = MemoryService(project_root)

    def status_text() -> str:
        return str(service.status())

    def proposal_text(proposal_id: str) -> str:
        return service.show(proposal_id)

    with gr.Blocks(title="Autobugfix Memory") as app:
        gr.Markdown("# Autobugfix Memory")
        refresh = gr.Button("Refresh")
        status = gr.Textbox(label="Status", lines=8)
        proposal_id = gr.Textbox(label="Proposal ID")
        show = gr.Button("Show")
        output = gr.Textbox(label="Output", lines=16)
        refresh.click(status_text, outputs=status)
        show.click(proposal_text, inputs=proposal_id, outputs=output)
    return app


def launch(host: str, port: int, project_root: Path | str = ".") -> None:
    create_app(project_root).launch(server_name=host, server_port=port)
