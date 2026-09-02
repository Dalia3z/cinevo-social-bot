/**
 * ecosystem.config.js
 * ===================
 * PM2 configuration to run the Cinevo AI Social Media Engagement Bot as a
 * persistent background service on a Linux VPS.
 *
 * Usage:
 *   pm2 start ecosystem.config.js
 *   pm2 save                 # persist the process list across reboots
 *   pm2 startup              # enable PM2 to start on boot (follow its output)
 *   pm2 logs cinevo-bot      # view logs
 *   pm2 restart cinevo-bot   # restart after code changes
 *   pm2 stop cinevo-bot      # stop the bot
 */

module.exports = {
  apps: [
    {
      name: "cinevo-bot",
      // Entry point of the bot.
      script: "main.py",
      // Use the Python interpreter from your virtualenv.
      interpreter: "python3",
      // Arguments passed to main.py (remove --dry-run for real posting).
      args: "",
      // Restart automatically if the process crashes.
      autorestart: true,
      // Restart the bot every 6 hours to keep it fresh and avoid leaks.
      max_restarts: 10,
      restart_delay: 5000,
      // Log files (PM2 also captures stdout/stderr here).
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      time: true,
      // Environment variables (optional; you can also use a .env file).
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1",
      },
      // Run only one instance to avoid duplicate replies.
      instances: 1,
      exec_mode: "fork",
    },
  ],
};
