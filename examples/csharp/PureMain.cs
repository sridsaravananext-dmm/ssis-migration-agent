namespace Demo
{
    public class ScriptMain : Microsoft.SqlServer.Dts.Tasks.ScriptTask.VSTARTScriptObjectModelBase
    {
        public void Main()
        {
            int count = (int)Dts.Variables["User::Count"].Value;
            if (count > 0 && count < 100)
            {
                Dts.Variables["User::Next"].Value = count + 1;
            }
            else
            {
                Dts.Variables["User::Next"].Value = -1;
            }
            Dts.TaskResult = 0;
        }
    }
}
