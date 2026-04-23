import re

def fix_diagrams(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Fix link syntax
    content = content.replace(
        'Backend -- "1. Remote Embeddings\\n(If Chroma on Render)" --> Inference',
        'Backend -->|"1. Remote Embeddings\\n(If Chroma on Render)"| Inference'
    )
    content = content.replace(
        'Backend -- "2. Read-only RAG\\n(VECTOR_BACKEND=hf)" --> Datasets',
        'Backend -->|"2. Read-only RAG\\n(VECTOR_BACKEND=hf)"| Datasets'
    )
    content = content.replace(
        'Backend -- "3. Data Lake Sync\\n(Historical Prices)" --> Datasets',
        'Backend -->|"3. Data Lake Sync\\n(Historical Prices)"| Datasets'
    )

    with open(filepath, 'w') as f:
        f.write(content)

fix_diagrams('docs/SYSTEM_DIAGRAMS.md')
fix_diagrams('frontend/src/SystemDiagramsUI.jsx')

# Fix Mermaid initialization to suppress global error rendering
with open('frontend/src/SystemDiagramsUI.jsx', 'r') as f:
    content = f.read()

content = content.replace(
    "startOnLoad: false,",
    "startOnLoad: false,\n    suppressErrorRendering: true,"
)

with open('frontend/src/SystemDiagramsUI.jsx', 'w') as f:
    f.write(content)
