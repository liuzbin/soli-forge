-- 1. 创建 users 表
CREATE TABLE IF NOT EXISTS `users` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255) NOT NULL,
  `hashed_password` VARCHAR(255) NOT NULL,
  `is_active` TINYINT(1) DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 创建 tasks 表
CREATE TABLE IF NOT EXISTS `tasks` (
  `id` VARCHAR(36) NOT NULL,
  `name` VARCHAR(100) DEFAULT NULL,
  `status` VARCHAR(20) DEFAULT 'created',
  `contract_name` VARCHAR(100) DEFAULT NULL,
  `source_code` TEXT,
  `exploit_code` TEXT,
  `fixed_code` TEXT,
  `slither_report` TEXT,
  `current_phase` VARCHAR(50) DEFAULT NULL,
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME DEFAULT NULL,
  `owner_id` INT DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_tasks_owner_id` (`owner_id`),
  CONSTRAINT `fk_tasks_owner_id` FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 创建 test_cases 表 (攻击矩阵用例)
CREATE TABLE IF NOT EXISTS `test_cases` (
  `id` VARCHAR(36) NOT NULL,
  `task_id` VARCHAR(36) NOT NULL,
  `source` VARCHAR(50) DEFAULT NULL COMMENT 'SLITHER, RED_TEAM, FUZZER',
  `name` VARCHAR(200) DEFAULT NULL,
  `description` TEXT,
  `code` TEXT,
  `status` VARCHAR(20) DEFAULT 'PENDING' COMMENT 'FAILING, PASSING, PENDING',
  `version_added` VARCHAR(10) DEFAULT 'v1',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_test_cases_task_id` (`task_id`),
  CONSTRAINT `fk_test_cases_task_id` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 创建 stream_logs 表 (实时日志)
CREATE TABLE IF NOT EXISTS `stream_logs` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `task_id` VARCHAR(36) NOT NULL,
  `timestamp` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `level` VARCHAR(10) DEFAULT 'INFO',
  `content` TEXT,
  PRIMARY KEY (`id`),
  KEY `ix_stream_logs_task_id` (`task_id`),
  CONSTRAINT `fk_stream_logs_task_id` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 创建 task_artifacts 表 (文件归档)
CREATE TABLE IF NOT EXISTS `task_artifacts` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `task_id` VARCHAR(36) NOT NULL,
  `artifact_type` VARCHAR(50) DEFAULT NULL,
  `filename` VARCHAR(255) DEFAULT NULL,
  `file_path` TEXT,
  `phase` VARCHAR(50) DEFAULT NULL,
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_task_artifacts_task_id` (`task_id`),
  CONSTRAINT `fk_task_artifacts_task_id` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;