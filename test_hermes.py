import ollama
import json

resp = ollama.chat(
    model="hermes3:latest",
    messages=[
        {"role": "system", "content": "You respond only with valid JSON."},
        {"role": "user", "content": 'Return this exact JSON: {"status": "ok", "model": "hermes3"}'},
    ],
    format="json",
    options={"temperature": 0.1},
)
result = json.loads(resp["message"]["content"])
print(json.dumps(result, indent=2))
print("Hermes OK")
