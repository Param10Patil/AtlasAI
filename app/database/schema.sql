create extension if not exists vector;

create table if not exists incidents (
  id uuid primary key,
  description text not null check (char_length(description) between 1 and 4000),
  service varchar(80),
  category varchar(80),
  severity varchar(16),
  created_at timestamptz not null
);

create table if not exists knowledge_documents (
  id uuid primary key,
  title varchar(200) not null,
  source varchar(300) not null unique,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  checksum varchar(64) not null unique
);

create table if not exists knowledge_chunks (
  id uuid primary key,
  document_id uuid not null references knowledge_documents(id) on delete cascade,
  content text not null,
  chunk_index integer not null check (chunk_index >= 0),
  embedding vector(8),
  metadata jsonb not null default '{}'::jsonb,
  unique(document_id, chunk_index)
);

create index if not exists knowledge_chunks_embedding_idx
  on knowledge_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 1);

create table if not exists historical_incidents (
  id uuid primary key,
  service varchar(80),
  category varchar(80) not null,
  summary varchar(500) not null,
  resolution varchar(500) not null,
  created_at timestamptz not null
);

create table if not exists analysis_results (
  id uuid primary key,
  request_id uuid not null unique,
  payload jsonb not null,
  created_at timestamptz not null
);
