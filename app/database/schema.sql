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

create table if not exists resolutions (
  id uuid primary key,
  incident_id uuid references incidents(id) on delete set null,
  status varchar(16) not null check (status in ('proposed', 'approved', 'executed', 'failed')),
  category varchar(80) not null,
  confidence double precision not null check (confidence between 0 and 1),
  payload jsonb not null,
  created_at timestamptz not null,
  completed_at timestamptz
);

create table if not exists analysis_jobs (
  id uuid primary key,
  client_request_id uuid unique,
  description text not null check (char_length(description) between 1 and 4000),
  status varchar(16) not null check (status in ('queued', 'running', 'complete', 'failed', 'cancelled')),
  created_at timestamptz not null,
  started_at timestamptz,
  completed_at timestamptz,
  cancel_requested boolean not null default false,
  attempt_count integer not null default 0 check (attempt_count between 0 and 3),
  lease_owner varchar(120),
  lease_expires_at timestamptz,
  error_code varchar(80),
  result_payload jsonb,
  incident_payload jsonb
);
alter table analysis_jobs add column if not exists incident_payload jsonb;

create index if not exists analysis_jobs_fifo_idx on analysis_jobs(status, created_at, id);
create unique index if not exists analysis_jobs_one_running_idx
  on analysis_jobs(status) where status = 'running';
