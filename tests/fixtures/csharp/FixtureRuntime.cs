// TEST FIXTURE ONLY. This is not an SSIS assembly or real runtime evidence.
using System.Collections.Generic;
namespace Microsoft.SqlServer.Dts.Tasks.ScriptTask
{
    public class VSTARTScriptObjectModelBase
    {
        public FixtureDts Dts = new FixtureDts();
    }
    public sealed class FixtureDts
    {
        public Dictionary<string, FixtureVariable> Variables = new Dictionary<string, FixtureVariable>();
        public int TaskResult = -999;
    }
    public sealed class FixtureVariable
    {
        public object Value;
        public FixtureVariable(object value) { Value = value; }
    }
}
