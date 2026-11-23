-- Chat en tiempo real con el First-line CX agent (WebSocket): ChatSession e historial de mensajes.
-- Modelos SQLModel: services/agent/chat/store.py

BEGIN;

CREATE TABLE IF NOT EXISTS public.chat_sessions (
    session_id VARCHAR(64) PRIMARY KEY,             -- el mismo id en el handshake, el canal chat.<session_id> y la memoria del agente
    agent_id   VARCHAR(40) NOT NULL DEFAULT 'first_line_cx' CHECK (agent_id IN ('first_line_cx')),
    user_id    VARCHAR(64) NOT NULL,
    client_id  VARCHAR(64),
    status     VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'interrupted', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON public.chat_sessions (user_id);

CREATE TABLE IF NOT EXISTS public.chat_messages (
    id         BIGSERIAL PRIMARY KEY,
    message_id VARCHAR(64) NOT NULL UNIQUE,         -- en las respuestas, también run_id del agente (trace y checkpoint)
    session_id VARCHAR(64) NOT NULL REFERENCES public.chat_sessions (session_id) ON DELETE CASCADE,
    role       VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT NOT NULL DEFAULT '',
    status     VARCHAR(16) NOT NULL CHECK (status IN ('streaming', 'completed', 'interrupted', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id ON public.chat_messages (session_id);

ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;

COMMIT;
