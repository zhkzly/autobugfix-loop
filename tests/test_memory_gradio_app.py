from autobugfix.memory_gradio_app import create_app


def test_memory_gradio_app_constructs(tmp_path):
    app = create_app(tmp_path)
    assert app is not None
