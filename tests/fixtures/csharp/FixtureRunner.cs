// TRUSTED TEST HARNESS ONLY. Never used to execute user-supplied project code.
using System;
using System.IO;
using System.Text.Json;
using System.Collections.Generic;
using Microsoft.SqlServer.Dts.Tasks.ScriptTask;
public static class FixtureRunner
{
    private static Dictionary<string, object> Snapshot(Demo.ScriptMain script)
    {
        var result = new Dictionary<string, object>();
        foreach (var pair in script.Dts.Variables) result[pair.Key] = pair.Value.Value;
        return result;
    }
    public static void Main(string[] args)
    {
        var results = new List<object>();
        using var input = JsonDocument.Parse(File.ReadAllText(args[0]));
        foreach (var test in input.RootElement.EnumerateArray())
        {
            var script = new Demo.ScriptMain();
            foreach (var property in test.GetProperty("input_variables").EnumerateObject())
            {
                object value = property.Value.ValueKind switch
                {
                    JsonValueKind.Number => property.Value.GetInt32(),
                    JsonValueKind.String => property.Value.GetString(),
                    JsonValueKind.True => true,
                    JsonValueKind.False => false,
                    _ => throw new InvalidOperationException("Unsupported fixture input")
                };
                script.Dts.Variables[property.Name] = new FixtureVariable(value);
            }
            object outcome;
            try
            {
                script.Main();
                outcome = new { status = "RETURNED", task_result = (int?)script.Dts.TaskResult,
                                variables = Snapshot(script), exception = (string)null };
            }
            catch (Exception error)
            {
                outcome = new { status = "THREW", task_result = (int?)null,
                                variables = Snapshot(script), exception = error.GetType().FullName };
            }
            results.Add(new { case_id = test.GetProperty("case_id").GetString(),
                              input_variables = test.GetProperty("input_variables"), outcome });
        }
        Console.Write(JsonSerializer.Serialize(results));
    }
}
