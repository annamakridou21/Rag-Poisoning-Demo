# Local LLM RAG Poisoning Demo

A small, local demonstration from my talk **Security Challenges in Local LLM Agent Architectures**. A fictional employee policy looks ordinary in Word, but its custom document properties contain an instruction aimed at the assistant. The retriever includes those properties in the model's context, crossing the boundary between document data and instructions.

## Run

Requires Python 3.10+ and [Ollama](https://ollama.com/) with a local model. The runtime uses only Python's standard library.

```powershell
ollama pull qwen2.5:14b
python -m agent_ctf --model qwen2.5:14b
```

Open <http://127.0.0.1:8765/> and ask **“Tell me how to get the new bonus.”** The server listens only on your computer. Stop it with Ctrl+C.

## What to inspect

- `knowledge_base/simple_rag/remote_work_update.docx`: fictional policy with a prompt-injection payload in its custom properties. In Word, open **File → Info → Properties → Advanced Properties → Custom**.
- `agent_ctf/rag.py`: extracts document text and custom properties for retrieval.
- `agent_ctf/agent.py`: the intentionally vulnerable RAG prompt sent to Ollama.
- `traces/`: local JSON records of the retrieved text, model request, raw model response, and displayed response. This directory is ignored by Git.

The UI labels a **scripted fallback** if the model does not ask for a password. The final “transfer” screen is also scripted. A fake follow-up value stays in the browser; it is not sent to the model or saved. **Never enter a real password.** Neither the demo nor its fictional company contacts a bank or moves money. Use `displayed_answer_source` in the trace to distinguish a model response from the fallback.

To rebuild the sample DOCX, install `python-docx` and run `python scripts/build_bonus_policy_demo.py`.
