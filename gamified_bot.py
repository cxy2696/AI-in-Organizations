import os
import json
import time
import asyncio
import urllib.request
import certifi
import ssl
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from github import Github, Auth
import discord
from discord.ext import commands
from aiohttp import TCPConnector
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables (set these in .env or system environment)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

class GamifiedGitHubDiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = TCPConnector(ssl=ssl_context)
        super().__init__(command_prefix='!', intents=intents, connector=connector)
        self.github = Github(auth=Auth.Token(GITHUB_TOKEN))
        self.repo = None
        self.init_db()
        self.user_data = self.load_user_data()
        self.last_global_check = datetime.now(timezone.utc) - timedelta(seconds=time.time())
        self.add_commands()
        self.validate_environment()

    def init_db(self):
        """Initialize SQLite database for persistent user data"""
        try:
            with sqlite3.connect('user_data.db') as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS users
                            (discord_id TEXT PRIMARY KEY, github_user TEXT, points INTEGER,
                             badges TEXT, current_challenge TEXT, last_activity_check TEXT)''')
                conn.commit()
                logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {str(e)}")

    def load_user_data(self):
        """Load user data from SQLite"""
        user_data = {}
        try:
            with sqlite3.connect('user_data.db') as conn:
                c = conn.cursor()
                c.execute("SELECT * FROM users")
                for row in c.fetchall():
                    discord_id, github_user, points, badges, challenge, last_check = row
                    user_data[int(discord_id)] = {
                        'github_user': github_user,
                        'points': points,
                        'badges': json.loads(badges) if badges else [],
                        'current_challenge': challenge,
                        'last_activity_check': datetime.fromisoformat(last_check) if last_check else self.last_global_check
                    }
            logger.info("User data loaded from database")
        except Exception as e:
            logger.error(f"Failed to load user data: {str(e)}")
        return user_data

    def save_user_data(self, discord_id, data):
        """Save user data to SQLite"""
        try:
            with sqlite3.connect('user_data.db') as conn:
                c = conn.cursor()
                c.execute('''INSERT OR REPLACE INTO users
                            (discord_id, github_user, points, badges, current_challenge, last_activity_check)
                            VALUES (?, ?, ?, ?, ?, ?)''',
                          (str(discord_id), data['github_user'], data['points'], json.dumps(data['badges']),
                           data['current_challenge'], data['last_activity_check'].isoformat()))
                conn.commit()
                logger.info(f"Saved data for Discord ID {discord_id}")
        except Exception as e:
            logger.error(f"Failed to save user data for {discord_id}: {str(e)}")

    def validate_environment(self):
        """Check if environment variables and API connections are valid"""
        if not all([GEMINI_API_KEY, GITHUB_TOKEN, DISCORD_BOT_TOKEN]):
            logger.error("Missing one or more environment variables")
            raise ValueError("Please set GEMINI_API_KEY, GITHUB_TOKEN, and DISCORD_BOT_TOKEN")
        try:
            self.github.get_user().login  # Test GitHub token
            logger.info("GitHub API connection validated")
        except Exception as e:
            logger.error(f"Invalid GitHub token: {str(e)}")
            raise

    def add_commands(self):
        @self.command(name='set_repo')
        async def set_repo(ctx, repo_name: str):
            """Set the GitHub repository to monitor (e.g., !set_repo owner/repo)"""
            try:
                self.repo = self.github.get_repo(repo_name)
                await ctx.send(f"Repository set to {repo_name}.")
                logger.info(f"Set repository to {repo_name}")
            except Exception as e:
                await ctx.send(f"Error setting repository: {str(e)}")
                logger.error(f"Error setting repository {repo_name}: {str(e)}")

        @self.command(name='link_github')
        async def link_github(ctx, github_username: str):
            """Link your Discord account to a GitHub username (e.g., !link_github myusername)"""
            if self.repo is None:
                await ctx.send("Please set the repository first with !set_repo.")
                return
            self.user_data[ctx.author.id] = {
                'github_user': github_username,
                'points': 0,
                'badges': [],
                'current_challenge': None,
                'last_activity_check': self.last_global_check
            }
            self.save_user_data(ctx.author.id, self.user_data[ctx.author.id])
            await ctx.send(f"Linked {ctx.author.name} to GitHub user {github_username}.")
            logger.info(f"Linked Discord user {ctx.author.name} to GitHub user {github_username}")

        @self.command(name='my_challenge')
        async def my_challenge(ctx):
            """Get a personalized challenge based on your GitHub activity"""
            if self.repo is None or ctx.author.id not in self.user_data:
                await ctx.send("Please set the repository and link your GitHub account first.")
                return
            user_info = self.user_data[ctx.author.id]
            activity = self.get_user_activity(user_info['github_user'])
            challenge = self.generate_challenge(activity)
            user_info['current_challenge'] = challenge
            self.save_user_data(ctx.author.id, user_info)
            await ctx.send(f"Your personalized challenge: {challenge}")
            logger.info(f"Generated challenge for {ctx.author.name}: {challenge}")

        @self.command(name='leaderboard')
        async def leaderboard(ctx):
            """Display the current leaderboard"""
            if not self.user_data:
                await ctx.send("No users linked yet.")
                return
            sorted_users = sorted(self.user_data.items(), key=lambda x: x[1]['points'], reverse=True)
            lb_text = "Leaderboard:\n"
            for idx, (disc_id, data) in enumerate(sorted_users, 1):
                user = await self.fetch_user(disc_id)
                lb_text += f"{idx}. {user.name} (@{data['github_user']}) - Points: {data['points']} | Badges: {', '.join(data['badges']) or 'None'}\n"
            await ctx.send(lb_text)
            logger.info("Displayed leaderboard")

        @self.command(name='sentiment')
        async def sentiment(ctx, message_id: int):
            """Analyze sentiment of a Discord message (e.g., !sentiment 1234567890)"""
            try:
                msg = await ctx.channel.fetch_message(message_id)
                sent = self.analyze_sentiment(msg.content)
                await ctx.send(f"Sentiment analysis: {sent}")
                logger.info(f"Sentiment analysis for message {message_id}: {sent}")
            except Exception as e:
                await ctx.send(f"Error: {str(e)}")
                logger.error(f"Error in sentiment analysis for message {message_id}: {str(e)}")

        @self.command(name='update_stats')
        async def update_stats(ctx):
            """Manually update stats and leaderboards"""
            await self.poll_github_once()
            await ctx.send("Stats updated.")
            logger.info("Manually updated stats")

        @self.command(name='shutdown')
        @commands.has_permissions(administrator=True)
        async def shutdown(ctx):
            """Shutdown the bot (admin only)"""
            await ctx.send("Shutting down the bot...")
            logger.info(f"Shutdown initiated by {ctx.author.name}")
            await self.close()

    async def setup_hook(self):
        self.bg_task = self.loop.create_task(self.poll_github_periodic())

    async def poll_github_once(self):
        logger.info("Starting GitHub poll")
        now = datetime.now(timezone.utc)
        for disc_id, data in self.user_data.items():
            gh_user = data['github_user']
            last_check = data['last_activity_check']
            new_points = 0
            try:
                # Commits
                commits = self.repo.get_commits(author=gh_user, since=last_check)
                new_points += commits.totalCount * 10
                # Issue comments
                comments = self.repo.get_issues_comments(since=last_check)
                user_comments = [c for c in comments if c.user.login == gh_user]
                new_points += len(user_comments) * 5
                # PR reviews
                prs = self.repo.get_pulls(state='all', sort='updated', direction='desc')
                for pr in prs[:10]:  # Limit to avoid rate limits
                    reviews = pr.get_reviews()
                    user_reviews = [r for r in reviews if r.user.login == gh_user and r.submitted_at > last_check]
                    new_points += len(user_reviews) * 15
                data['points'] += new_points
                # Update badges
                self.update_badges(data)
                # Check if challenge completed
                if data['current_challenge'] and new_points > 0:
                    data['points'] += 20  # Bonus for completing challenge
                    data['current_challenge'] = None
                data['last_activity_check'] = now
                self.save_user_data(disc_id, data)
                logger.info(f"Updated stats for {gh_user}: {new_points} new points")
            except Exception as e:
                logger.error(f"Error polling GitHub for {gh_user}: {str(e)}")
        self.last_global_check = now
        logger.info("GitHub poll completed")

    async def poll_github_periodic(self):
        await self.wait_until_ready()
        while not self.is_closed():
            if self.repo:
                await self.poll_github_once()
            await asyncio.sleep(300)  # Poll every 5 minutes

    def get_user_activity(self, gh_user):
        """Get a summary of user activity"""
        try:
            commits = self.repo.get_commits(author=gh_user).totalCount
            issues = self.repo.get_issues(creator=gh_user).totalCount
            prs = self.repo.get_pulls(head=f"{gh_user}:*", state='all').totalCount
            logger.info(f"Fetched activity for {gh_user}: Commits={commits}, Issues={issues}, PRs={prs}")
            return f"Commits: {commits}, Issues created: {issues}, PRs: {prs}"
        except Exception as e:
            logger.error(f"Error fetching activity for {gh_user}: {str(e)}")
            return f"Error fetching activity: {str(e)}"

    def generate_challenge(self, activity):
        """Use Gemini to generate personalized challenge"""
        prompt = f"Based on this GitHub user activity: {activity}. Generate a personalized, engaging challenge to boost collaboration, e.g., 'Review one PR to earn a collaborator badge'. Keep it short."
        return self.call_gemini(prompt)

    def analyze_sentiment(self, text):
        """Use Gemini for sentiment analysis"""
        prompt = f"Analyze the sentiment of this discussion text: '{text}'. Provide a summary like 'Positive: encouraging collaboration' or 'Negative: frustration detected'. Consider biases and be neutral."
        return self.call_gemini(prompt)

    def call_gemini(self, prompt):
        """Call Google Gemini API with rate limit handling"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        context = ssl.create_default_context(cafile=certifi.where())
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, context=context) as response:
                    result = json.loads(response.read().decode())
                    response_text = result['candidates'][0]['content']['parts'][0]['text'].strip()
                    logger.info(f"Gemini API call successful: {response_text[:50]}...")
                    return response_text
            except Exception as e:
                if "429" in str(e):  # Rate limit
                    logger.warning(f"Gemini rate limit hit, retrying in {2 ** attempt}s")
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                logger.error(f"Gemini API error: {str(e)}")
                return f"Error calling AI: {str(e)}"
        logger.error("Gemini API rate limit exceeded after retries")
        return "Error: Gemini API rate limit exceeded."

    def update_badges(self, user_data):
        points = user_data['points']
        badges = user_data['badges']
        if points >= 10 and 'Bronze Collaborator' not in badges:
            badges.append('Bronze Collaborator')
        if points >= 50 and 'Silver Collaborator' not in badges:
            badges.append('Silver Collaborator')
        if points >= 100 and 'Gold Collaborator' not in badges:
            badges.append('Gold Collaborator')

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

async def main():
    try:
        bot = GamifiedGitHubDiscordBot()
        await bot.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    # Check if running in an existing event loop (e.g., Jupyter)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(main())
    except RuntimeError:
        # No running loop, use asyncio.run
        asyncio.run(main())