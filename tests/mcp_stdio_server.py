import json
import sys


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "tools": {},
                        "resources": {"listChanged": True},
                        "prompts": {"listChanged": True},
                    },
                    "serverInfo": {"name": "fake", "version": "1"},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "write",
                            "description": "Write text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                        },
                    ],
                },
            })
        elif method == "resources/list":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resources": [
                        {
                            "uri": "file:///docs/readme.txt",
                            "name": "readme.txt",
                            "mimeType": "text/plain",
                        },
                        {
                            "uri": "file:///docs/logo.png",
                            "name": "logo.png",
                            "mimeType": "image/png",
                        },
                    ],
                },
            })
        elif method == "resources/templates/list":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resourceTemplates": [
                        {
                            "uriTemplate": "file:///docs/{name}",
                            "name": "docs",
                        },
                    ],
                },
            })
        elif method == "resources/read":
            params = message.get("params") or {}
            uri = str(params.get("uri") or "")
            if uri.endswith("logo.png"):
                result = {
                    "contents": [{
                        "uri": uri,
                        "mimeType": "image/png",
                        "blob": "iVBORw0KGgo=",
                    }],
                }
            else:
                result = {
                    "contents": [{
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": "resource text for " + uri,
                    }],
                }
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "prompts/list":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "prompts": [{
                        "name": "code-review",
                        "description": "Review code",
                        "arguments": [
                            {"name": "language", "required": True},
                            {"name": "focus", "required": False},
                        ],
                    }],
                },
            })
        elif method == "prompts/get":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "description": "Review code",
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": "review " + str(arguments.get("language", ""))
                                + " focus " + str(arguments.get("focus", "")),
                            },
                        },
                    ],
                },
            })
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": "echo: " + str(arguments.get("text", ""))}],
                    },
                })
            elif name == "write":
                send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": "written: " + str(arguments.get("text", ""))}],
                    },
                })
            else:
                send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "unknown tool"},
                })
        elif request_id is not None:
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not supported"},
            })


if __name__ == "__main__":
    main()
