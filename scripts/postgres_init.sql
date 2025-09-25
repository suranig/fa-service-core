-- PostgreSQL initialization script
-- Sets up extensions and basic configuration

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "citext";

-- Create application user if needed (optional, for production)
-- CREATE USER fa_app WITH PASSWORD 'fa_app_password';
-- GRANT CONNECT ON DATABASE fa_cms TO fa_app;

-- Set up basic configuration
-- ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';
-- ALTER SYSTEM SET log_statement = 'all';
-- ALTER SYSTEM SET log_min_duration_statement = 100;

-- Reload configuration
-- SELECT pg_reload_conf();
