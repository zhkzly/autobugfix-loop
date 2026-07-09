from autobugfix.gradio_app import create_app
from tests.helpers import make_service_project


def test_gradio_app_constructs(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    app = create_app(project_root)
    assert app is not None
