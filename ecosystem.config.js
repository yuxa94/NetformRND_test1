// pm2 delete netform-rnd
// pm2 start ecosystem.config.js
// pm2 save

module.exports = {
  apps: [
    {
      name: "netform-rnd",
      script: "gunicorn",
      args: "server:app --bind 0.0.0.0:8000 --workers 2",
      interpreter: "none",
      env: {
        FLASK_ENV: "production",
      },
    },
  ],
};
