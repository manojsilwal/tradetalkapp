-- Run in Supabase SQL editor before enabling VECTOR_BACKEND=supabase.
-- This creates a shared vector memory table and a pgvector match function.

create extension if not exists vector;

create table if not exists public.vector_memory (
  id text primary key,
  collection text not null,
  document text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1536),
  created_at timestamptz not null default now()
);

create index if not exists vector_memory_collection_idx
  on public.vector_memory (collection);

create index if not exists vector_memory_created_at_idx
  on public.vector_memory (created_at desc);

create index if not exists vector_memory_metadata_gin_idx
  on public.vector_memory using gin (metadata);

create or replace function public.match_vector_memory(
  query_embedding vector(1536),
  match_count int,
  in_collection text,
  metadata_filter jsonb default '{}'::jsonb
)
returns table (
  id text,
  document text,
  metadata jsonb,
  distance float
)
language sql
stable
as $$
  select
    vm.id,
    vm.document,
    vm.metadata,
    (vm.embedding <=> query_embedding) as distance
  from public.vector_memory vm
  where vm.collection = in_collection
    and (metadata_filter = '{}'::jsonb or vm.metadata @> metadata_filter)
    and vm.embedding is not null
  order by vm.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;
