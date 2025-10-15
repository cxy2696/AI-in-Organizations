import os
import json
import time
import asyncio
import urllib.request
import certifi
import ssl
from datetime import datetime, timezone, timedelta
from github import Github
import discord
from discord.ext import commands
from aiohttp import TCPConnector


#os.environ['GEMINI_API_KEY'] = 
#os.environ['GITHUB_TOKEN'] = 
#os.environ['DISCORD_BOT_TOKEN'] = 

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
        self.github = Github(GITHUB_TOKEN)
        self.repo = None  # Placeholder: 'owner/repo'
        self.user_data = {}  # discord_id: {'github_user': str, 'points': int, 'badges': list, 'current_challenge': str, 'last_activity_check': datetime}
        self.last_global_check = datetime.now(timezone.utc) - timedelta(seconds=time.time())  # Initial backoff (fixed)
        self.add_commands()

    def add_commands(self):
        @self.command(name='set_repo')
        async def set_repo(ctx, repo_name: str):
            """Set the GitHub repository to monitor (e.g., !set_repo owner/repo)"""
            try:
                self.repo = self.github.get_repo(repo_name)
                await ctx.send(f"Repository set to {repo_name}.")
            except Exception as e:
                await ctx.send(f"Error setting repository: {str(e)}")

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
            await ctx.send(f"Linked {ctx.author.name} to GitHub user {github_username}.")

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
            await ctx.send(f"Your personalized challenge: {challenge}")

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

        @self.command(name='sentiment')
        async def sentiment(ctx, message_id: int):
            """Analyze sentiment of a Discord message (e.g., !sentiment 1234567890)"""
            try:
                msg = await ctx.channel.fetch_message(message_id)
                sent = self.analyze_sentiment(msg.content)
                await ctx.send(f"Sentiment analysis: {sent}")
            except Exception as e:
                await ctx.send(f"Error: {str(e)}")

        @self.command(name='update_stats')
        async def update_stats(ctx):
            """Manually update stats and leaderboards"""
            await self.poll_github_once()
            await ctx.send("Stats updated.")

    async def setup_hook(self):
        self.bg_task = self.loop.create_task(self.poll_github_periodic())

    async def poll_github_periodic(self):
        await self.wait_until_ready()
        while not self.is_closed():
            if self.repo:
                await self.poll_github_once()
            await asyncio.sleep(300)  # Poll every 5 minutes

    async def poll_github_once(self):
        now = datetime.now(timezone.utc)
        for disc_id, data in self.user_data.items():
            gh_user = data['github_user']
            last_check = data['last_activity_check']
            # Update points based on new activity
            new_points = 0
            # Commits
            commits = self.repo.get_commits(author=gh_user, since=last_check)
            new_points += commits.totalCount * 10
            # Issue comments
            comments = self.repo.get_issues_comments(since=last_check)
            user_comments = [c for c in comments if c.user.login == gh_user]
            new_points += len(user_comments) * 5
            # PR reviews (simplified: count review comments)
            prs = self.repo.get_pulls(state='all', sort='updated', direction='desc')
            for pr in prs[:10]:  # Limit to recent to avoid rate limits
                reviews = pr.get_reviews()
                user_reviews = [r for r in reviews if r.user.login == gh_user and r.submitted_at > last_check]
                new_points += len(user_reviews) * 15
            data['points'] += new_points
            # Update badges
            self.update_badges(data)
            # Check if challenge completed (simple: if new_points > 0 and challenge exists)
            if data['current_challenge'] and new_points > 0:
                data['points'] += 20  # Bonus for completing challenge
                data['current_challenge'] = None
            data['last_activity_check'] = now
        self.last_global_check = now

    def get_user_activity(self, gh_user):
        """Get a summary of user activity"""
        try:
            commits = self.repo.get_commits(author=gh_user).totalCount
            issues = self.repo.get_issues(creator=gh_user).totalCount
            prs = self.repo.get_pulls(head=f"{gh_user}:*", state='all').totalCount
            return f"Commits: {commits}, Issues created: {issues}, PRs: {prs}"
        except Exception as e:
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
        """Call Google Gemini API"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-latest:generateContent?key={GEMINI_API_KEY}"
        data = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urllib.request.urlopen(req, context=context) as response:
                result = json.loads(response.read().decode())
                return result['candidates'][0]['content']['parts'][0]['text'].strip()
        except Exception as e:
            return f"Error calling AI: {str(e)}"

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
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

# Run the bot
bot = GamifiedGitHubDiscordBot()
bot.run(DISCORD_BOT_TOKEN)