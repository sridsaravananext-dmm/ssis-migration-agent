# Bounded C# example

`PureMain.cs` is synthetic source for offline tests and demonstrations. It is
not a user export or verified business rule. Do not execute it against SSIS.

The candidate generator requires a versioned migration contract containing this
source in its normalized graph, plus an explicit `metadata.csharp_translation`
specification. Bind source node/locator/SHA-256 to the actual imported evidence.
Supply separate evidence for `Demo.ScriptMain.Main`, the ScriptTask runtime,
unchecked integer overflow, success value `0`, and these sample variable types:

| Sample variable | Type | Access |
| --- | --- | --- |
| `User::Count` | `int32` | `READ` |
| `User::Next` | `int32` | `WRITE` |

These values describe this synthetic fixture only. They are never defaults for
real source packages. See `docs/phase7-csharp-syntax-translation.md` for the
contract interface and support boundary. Generated functions require a supplied
state dictionary and explicit caller/job integration; defining one is not a
completed migration. No execution or acceptance is inferred from code generation.
