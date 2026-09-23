from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = spec_from_file_location('ui_fixture', ROOT/'tests/fixtures/ui/make_fixture.py')
module = module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def sample(tmp_path):
    output, data = tmp_path/'outputs', tmp_path/'data'
    gids = module.make_fixture(output, data)
    from src.bundle_io import resolve_run
    return resolve_run(output)[0], data, gids
