import { defineRailway, postgres, preserve, project, redis, service } from "railway/iac";

export default defineRailway(() => {
  const pg = postgres("Postgres");
  const rd = redis("Redis");

  const sharedVars = {
    APP_ENV: preserve(),
    API_FOOTBALL_KEY: preserve(),
    API_FOOTBALL_HOST: preserve(),
    LOG_LEVEL: preserve(),
    SECRET_KEY: preserve(),
    RESEARCH_API_KEY: preserve(),
  };

  const web = service("web", {
    // Top-level deploy settings (normalizeDeploy reads config.preDeploy, config.healthcheck etc.)
    preDeploy: "alembic upgrade head",
    healthcheck: "/health",
    healthcheckTimeout: 300,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "backend/Dockerfile",
    },
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    variables: {
      ...sharedVars,
      DATABASE_URL: preserve(),
      REDIS_URL: preserve(),
    },
  });

  const worker = service("worker", {
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "backend/Dockerfile",
    },
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    variables: {
      ...sharedVars,
      DATABASE_URL: preserve(),
      REDIS_URL: preserve(),
      CELERY_WORKER: preserve(),
    },
  });

  return project("lohela", {
    resources: [pg, rd, web, worker],
  });
});
