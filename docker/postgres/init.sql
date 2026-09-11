-- BARIYON Receptra - PostgreSQL Initialization

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Set timezone
SET timezone = 'Asia/Tokyo';

-- Initial database setup is complete
COMMENT ON DATABASE receptra IS 'BARIYON Receptra - AI Reception Platform Database';
