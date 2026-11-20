Ya construiste las cuatro funciones de tu sistema RAG (setup, embed, retrieve, query) y las expusiste como un endpoint de FastAPI. Funciona, pero es una caja negra: recibe una pregunta y devuelve una respuesta, sin que nadie —ni tú— pueda ver qué decisiones tomó en el camino.

Tu tech lead abrió un ticket con un requisito claro: antes de agregar cualquier capacidad nueva al agente (Parte 2 de este mismo proyecto), el flujo de razonamiento tiene que quedar explícito como un grafo, con estado, nodos y transiciones que se puedan trazar y evaluar de forma independiente.

Nota del tech lead: "No quiero que reescribas la lógica del RAG desde cero — el retrieve y el embed que ya tienes funcionan. Lo que quiero es que ese mismo comportamiento viva dentro de un grafo de LangGraph, con nodos con una sola responsabilidad, y que cada corrida quede trazada. Si no puedo ver por qué el agente respondió lo que respondió, no puedo confiar en él en producción."

Tres cosas quedan implícitas en ese ticket y son fáciles de pasar por alto: (1) el grafo debe compilarse antes de cualquier ejecución, para detectar errores estructurales en tiempo de build y no en producción; (2) el estado que viaja entre nodos debe ser mínimo y explícito, no el historial completo de la conversación; y (3) cada corrida debe producir un trace consultable, no solo una respuesta final.

Conocimiento complementario: del loop ingenuo al grafo
Un agente "ingenuo" es simplemente un while en Python: llamas al modelo, si pide una tool la ejecutas, le devuelves el resultado, y repites. Funciona para prototipos, pero no escala: no hay forma de pausar, retomar, trazar un paso específico o testear un nodo de forma aislada.

LangGraph formaliza ese mismo loop como una máquina de estados: cada paso es un nodo, cada decisión sobre a dónde ir después es una arista (edge), y el conjunto compilado es el grafo. Esto es lo que te permite, en la Parte 2, agregar una tool nueva sin tocar el resto del flujo: solo agregas un nodo y una arista condicional que decide cuándo usarla.

Instala la dependencia con uv add langgraph (nunca uses pip install ni pipenv).
Ubica tu código RAG existente: tus funciones setup, embed, retrieve y query en data/pipelines/, y el endpoint que las expone en services/.