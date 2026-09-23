"""Downloaded CSV retains Unicode and advertises UTF-8 to desktop readers."""
from codecs import BOM_UTF8
from io import BytesIO
from pathlib import Path
import sys

import pandas as pd
from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest

from backend.core.csv_export import csv_download_bytes
from backend.core.results import load_bundle


def test_csv_download_roundtrip_and_existing_signature():
    text = 'gid,evidence\n9007199254740993,выход 16685.00 KZT. Только наблюдаемые переводы ≥5000 KZT.\n'
    raw = text.encode('utf-8')
    for source in (raw, BOM_UTF8 + raw):
        exported = csv_download_bytes(source)
        assert exported.startswith(BOM_UTF8)
        assert exported.decode('utf-8-sig') == text
        frame = pd.read_csv(BytesIO(exported), dtype={'gid': str})
        assert frame.gid.tolist() == ['9007199254740993']
        assert frame.evidence.iloc[0] == 'выход 16685.00 KZT. Только наблюдаемые переводы ≥5000 KZT.'


def test_streamlit_downloads_include_utf8_signature(sample, monkeypatch):
    output, _, _ = sample
    captured = {}
    original = DeltaGenerator.download_button

    def capture(self, label, data, file_name=None, mime=None, **kwargs):
        captured[file_name] = (data, mime)
        return original(self, label, data, file_name=file_name, mime=mime, **kwargs)

    monkeypatch.setattr(DeltaGenerator, 'download_button', capture)
    monkeypatch.setattr(sys, 'argv', ['app.py', '--outputs', str(output)])
    app_path = Path(__file__).resolve().parents[1] / 'frontend' / 'app.py'
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    assert not app.exception
    bundle = load_bundle(output)
    assert set(captured) == {'nodes_roles.csv', 'top_nodes.csv', 'clusters.csv'}
    for filename, (raw, mime) in captured.items():
        assert raw.startswith(BOM_UTF8)
        assert raw.decode('utf-8-sig') == bundle.raw[filename].decode('utf-8-sig')
        assert mime == 'text/csv; charset=utf-8'
