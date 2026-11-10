"""Exercise result rendering on the supported Streamlit versions."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('dry_run', [True, False])
def test_pending_results_render_without_nested_expanders_and_show_run_state(dry_run):
    app = AppTest.from_file(str(ROOT/'frontend/app.py'), default_timeout=30).run()
    assert not app.exception
    app.radio[0].set_value('Use WWI sample package')
    next(w for w in app.checkbox if w.label == 'Strict mode').check()
    next(w for w in app.checkbox if w.label == 'Dry run').set_value(dry_run)
    app.run()
    action = 'Preview assessment' if dry_run else 'Analyze & generate'
    next(w for w in app.button if w.label == action).click().run()
    assert not app.exception, [e.message for e in app.exception]
    report = app.session_state['result']
    assert report['failed_package_count'] == 0
    item = report['packages'][0]
    pending = json.loads(Path(item['artifacts']['pending_items']).read_text())
    assert pending  # The failing UI path must actually render its expander.
    count = len(pending)
    assert item['assessment']['planning']['pending_section_count'] == count
    assert any(e.label == f'Pending implementation sections ({count})' for e in app.expander)
    assert any(m.value == f'Pending implementation sections: {count}' for m in app.markdown)
    labels = [b.proto.label for b in app.get('download_button')]
    assert 'Download review summary' in labels
    if dry_run:
        assert item['assessment']['generation']['status'] == 'NOT_RUN'
        assert any('static code checks were not run' in message.value for message in app.info)
        assert not any(m.value.startswith('Operations passing static checks:') for m in app.markdown)
        assert 'Download notebook draft' not in labels
        # Changing settings without running again must not relabel existing results.
        next(w for w in app.checkbox if w.label == 'Dry run').uncheck().run()
        assert not app.exception
        assert any('static code checks were not run' in message.value for message in app.info)
    else:
        assert item['assessment']['generation']['status'] == 'ASSESSED'
        assert item['assessment']['generation']['pending_operations'] > 0
        assert 'Download notebook draft' in labels
        assert not any('static code checks were not run' in message.value for message in app.info)


def test_manually_edited_draft_is_preserved_and_remains_downloadable():
    app=AppTest.from_file(str(ROOT/'frontend/app.py'),default_timeout=30).run()
    app.radio[0].set_value('Use WWI sample package').run()
    next(w for w in app.button if w.label=='Analyze & generate').click().run()
    assert not app.exception
    path=Path(app.session_state['result']['packages'][0]['artifacts']['notebook'])
    notebook=json.loads(path.read_text())
    notebook['cells'].append({'cell_type':'markdown','metadata':{},'source':['My manual implementation notes.']})
    path.write_text(json.dumps(notebook))
    edited=path.read_bytes()
    next(w for w in app.button if w.label=='Analyze & generate').click().run()
    assert not app.exception
    report=app.session_state['result']
    assert report['preserved_package_count']==1 and report['generated_package_count']==0
    assert path.read_bytes()==edited
    assert any('NOTEBOOK_CONTENT_CHANGED' in m.value and 'REVALIDATION_REQUIRED' in m.value for m in app.markdown)
    assert 'Download notebook draft' in [b.proto.label for b in app.get('download_button')]


def test_native_binding_requirements_render_on_the_results_card(tmp_path):
    import tempfile
    from ssis_migration.migrate import run_migration
    source=tmp_path/'native.dtsx'
    source.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
      xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask" DTS:refId="Package" DTS:ObjectName="Native">
      <DTS:Variables><DTS:Variable DTS:ObjectName="Result" DTS:Namespace="User"><DTS:VariableValue DTS:DataType="8"/></DTS:Variable></DTS:Variables>
      <DTS:Executables><DTS:Executable DTS:refId="Package\\T" DTS:ExecutableType="Microsoft.ExecuteSQLTask"><DTS:ObjectData>
        <SQLTask:SqlTaskData SQLTask:SqlStatementSource="SELECT @name AS name" SQLTask:ResultType="ResultSetType_SingleRow">
          <SQLTask:ParameterBinding SQLTask:ParameterName="@name" SQLTask:DtsVariableName="System::PackageName" SQLTask:ParameterDirection="Input"/>
          <SQLTask:ResultBinding SQLTask:ResultName="0" SQLTask:DtsVariableName="User::Result"/>
        </SQLTask:SqlTaskData></DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    report=run_migration(str(source),str(tmp_path/'out'))
    assert report['packages'][0]['assessment']['runtime_bindings']['execution_mode']=='SINGLE_NOTEBOOK'
    app=AppTest.from_file(str(ROOT/'frontend/app.py'),default_timeout=30).run()
    (Path(tempfile.gettempdir())/'ssis_conversion_app'/app.session_state['session_id']).mkdir(parents=True,exist_ok=True)
    app.session_state['result']=report
    app.session_state['output']=str(tmp_path/'out')
    app.run()
    assert not app.exception
    assert any(e.label=='Native runtime bindings' for e in app.expander)
