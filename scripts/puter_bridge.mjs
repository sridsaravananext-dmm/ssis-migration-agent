import {
    init,
    getAuthToken,
} from "@heyputer/puter.js/src/init.cjs";

const model =
    process.env.PUTER_MODEL ||
    "gpt-5.6-luna";

async function readStdin() {
    const chunks = [];

    for await (const chunk of process.stdin) {
        chunks.push(chunk);
    }

    return Buffer.concat(chunks).toString("utf8");
}

try {
    const prompt = await readStdin();

    if (!prompt.trim()) {
        throw new Error("Prompt is empty.");
    }

    console.error("[puter] authenticating...");
    const authToken = await getAuthToken();

    if (!authToken) {
        throw new Error(
            "Puter authentication returned no token."
        );
    }

    console.error("[puter] authentication successful");
    console.error(`[puter] model=${model}`);

    const puter = init(authToken);

    const response = await puter.ai.chat(
        prompt,
        {
            model,
            normalize: true,
        }
    );

    let output;

    if (typeof response === "string") {
        output = response;
    } else if (response?.message?.content) {
        output = response.message.content;
    } else if (response?.content) {
        output = response.content;
    } else {
        output = JSON.stringify(response);
    }

    process.stdout.write(output);
    process.exit(0);

} catch (error) {
    console.error(
        JSON.stringify({
            error_message: error?.message || null,
            error_string: String(error),
            error_name: error?.name || null,
            error_constructor: error?.constructor?.name || null,
            error_keys: error ? Object.keys(error) : [],
            error_stack: error?.stack || null,
        }, null, 2)
    );

    process.exit(1);
} 