# NarrativeGraph

NarrativeGraph is an NLP and LLM-based application that analyzes literary texts, identifies characters and their interactions, and visualizes relationships as an interactive graph.

## Architecture

1. **NLP Processing** - segments text into sentences and prepares character mentions for analysis.
2. **LLM Extraction** - identifies characters, resolves references, and extracts relationships.
3. **AI Agent** - retrieves additional context and checks ambiguous relationships when needed.
4. **Validation** - checks extracted relationships against supporting excerpts from the original text.

The web interface displays an interactive character graph, highlighted mentions, evidence excerpts, and agent activity.

## How to Run

Requires Python 3.10+ and a Gemini API key.

```bash
pip install -r requirements.txt
```

Set `API_KEY` in `local_config.py`, then run:

```bash
python3 server.py
```

Open http://127.0.0.1:8787

## Evaluation

The project includes test cases for character and relationship extraction and an evaluation script using Precision, Recall, and F1.

```bash
python3 -m evaluation.evaluate
```

**Limitation:** Ambiguous pronouns and relationships may remain unresolved when the text does not provide sufficient context.
