# connections
import os
from dotenv import load_dotenv

# data management
import aiomysql
import asyncio
import pandas as pd
import pytz
import re
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta

# internal
import logging
import bot_camera
import bot_queries
from sql_runners import get_df_from_sql, send_df_to_sql

# set up logging?
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# get secrets
load_dotenv()
NYT_COOKIE = os.getenv('NYT_COOKIE')

# get current time
def get_current_time():
    now = datetime.now(pytz.timezone('US/Eastern'))
    return now.strftime("%Y-%m-%d %H:%M:%S")

# function to both print messages and save them to the log file
def bot_print(message):
    
    # add timestamp to message
    msg = f"{get_current_time()}: {message}"
    
    # print and log message
    print(msg)
    logger.info(msg)

# find main channel id for each guild (old)
async def get_bot_channels():
    query = """
        SELECT guild_id, channel_id, guild_channel_category
        FROM discord_connections
        WHERE guild_channel_category = 'main'
    """
    
    # Get the DataFrame from the SQL query
    df = await get_df_from_sql(query)

    # Initialize bot_channels dictionary
    bot_channels = {}

    # Iterate over DataFrame rows and populate the dictionary
    for index, row in df.iterrows():
        bot_channels[row["guild_id"]] = {
            "channel_id": row["channel_id"],
            "channel_id_int": int(row["channel_id"]),
        }

    return bot_channels

# get mini date
def get_mini_date():
    now = datetime.now(pytz.timezone('US/Eastern'))
    cutoff_hour = 17 if now.weekday() in [5, 6] else 21
    if now.hour > cutoff_hour:
        return (now + timedelta(days=1)).date()
    else:
        return now.date()

# translate date range based on text
def get_date_range(user_input):
    today = datetime.now(pytz.timezone('US/Eastern')).date()

    # Helper function to parse date string and set year to current year if not provided
    def parse_date(date_str, default_year=today.year):
        date_obj = parse(date_str)
        if date_obj.year == 1900:  # dateutil's default year is 1900 when not provided
            date_obj = date_obj.replace(year=default_year)
        return date_obj.date()

    try:
        if user_input == 'today':
            min_date = max_date = today
        elif user_input == 'yesterday':
            min_date = max_date = today - timedelta(days=1)
        elif user_input == 'last week':
            min_date = today - timedelta(days=today.weekday(), weeks=1)
            max_date = min_date + timedelta(days=6)
        elif user_input == 'this week':
            min_date = today - timedelta(days=today.weekday())
            max_date = today - timedelta(days=1)
        elif user_input == 'this month':
            min_date = today.replace(day=1)
            max_date = today - timedelta(days=1)
        elif user_input == 'last month':
            min_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            max_date = (min_date.replace(month=min_date.month % 12 + 1) - timedelta(days=1))
        elif user_input == 'this year':
            min_date = today.replace(month=1, day=1)
            max_date = today - timedelta(days=1)
        elif user_input == 'last year':
            min_date = today.replace(year=today.year - 1, month=1, day=1)
            max_date = today.replace(year=today.year - 1, month=12, day=31)
        elif user_input == 'all time':
            min_date = date.min
            max_date = date.max
        else:
            dates = [parse_date(d.strip()) for d in user_input.split(':')]
            min_date, max_date = (dates[0], dates[-1]) if len(dates) > 1 else (dates[0], dates[0])
# if you find this comment, you win a prize!
    except(ValueError, TypeError):
        return None
    
    return min_date, max_date

# returns image location of leaderboard
async def get_leaderboard(guild_id, game_name, min_date=None, max_date=None, user_nm=None):

    today = datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d")

    # if no date range, use default
    if min_date is None and max_date is None:
        if game_name == 'mini':
            min_date = max_date = get_mini_date().strftime("%Y-%m-%d")
        else:
            min_date, max_date = today, today
    else:
        min_date = min_date.strftime("%Y-%m-%d")
        max_date = max_date.strftime("%Y-%m-%d")
    
    # format the title
    if min_date == max_date:
        title_date = min_date
    else:
        title_date = f"{min_date} through {max_date}"

    # determine leaderboard query to run
    cols, query, params = bot_queries.build_query(guild_id, game_name, min_date, max_date, user_nm)
    
    try:
        
        # new asynchronous query function
        df = await get_df_from_sql(query, params)
        print('got query into dataframe')

    except Exception as e:
        print(f"Error when trying to run SQL query: {e}")
        img = 'files/images/error.png'
        return img

    if not df.empty and cols:
        df.columns = cols

    # clean some columns
    if 'Rank' in df.columns:
        df['Rank'] = df['Rank'].fillna('').astype(str).apply(lambda x: x.rstrip('.0') if '.' in x and x != '' else x)
    if 'Game' in df.columns:
        df['Game'] = df['Game'].str.capitalize()

    # create image
    img_title = game_name.capitalize() if game_name != 'my_scores' else user_nm
    img = bot_camera.dataframe_to_image_dark_mode(df, img_title=img_title, img_subtitle=title_date)
    return img

# add discord scores to database when people paste them to discord chat
async def add_score(game_prefix, game_date, discord_id, msg_txt):

    # get date and time
    now = datetime.now(pytz.timezone('US/Eastern'))
    added_ts = now.strftime("%Y-%m-%d %H:%M:%S")

    # set these up
    game_name = None
    game_score = None
    game_dtl = None
    metric_01 = None
    metric_02 = None # game completed yes/no
    metric_03 = None

    if game_prefix == "#Worldle":
        game_name = "worldle"
        game_score = msg_txt[14:17]

    if game_prefix == "Wordle":
        game_name = "wordle"

        # find position slash for the score
        found_score = msg_txt.find('/')
        if found_score == -1:
            msg_back = [False, 'Invalid format']
            return msg_back
        else:
            game_score = msg_txt[11:14]
            metric_02 = 1 if game_score[0] != 'X' else 0

    if game_prefix in ["#travle", "#travle_usa", "#travle_gbr"]:
        game_name = game_prefix[1:]

        # find position of opening and closing parentheses
        opening_paren = msg_txt.find('(')
        closing_paren = msg_txt.find(')')

        # get substring between parentheses
        game_score = msg_txt[opening_paren+1:closing_paren]

        # set metric_02 based on first character of game_score
        metric_02 = 1 if game_score[0] != '?' else 0

    if game_prefix == "Factle.app":
        game_name = "factle"
        game_score = msg_txt[14:17]
        game_dtl = msg_txt.splitlines()[1]
        lines = msg_txt.split('\n')

        # find green frogs
        g1, g2, g3, g4, g5 = 0, 0, 0, 0, 0
        for line in lines[2:]:
            if line[0] == '🐸':
                g1 = 1
            if line[1] == '🐸':
                g2 = 1
            if line[2] == '🐸':
                g3 = 1
            if line[3] == '🐸':
                g4 = 1
            if line[4] == '🐸':
                g5 = 1
        metric_03 = g1 + g2 + g3 + g4 + g5

        # get top X% denoting a win
        final_line = lines[-1]
        if "Top" in final_line:
            metric_01 = final_line[4:]
            metric_02 = 1
        else:
            game_score = 'X/5'
            metric_02 = 0

    if game_prefix == 'boxofficega.me':
        game_name = 'boxoffice'
        game_dtl = msg_txt.split('\n')[1]
        movies_guessed = 0
        trophy_symbol = u'\U0001f3c6'
        check_mark = u'\u2705'

        # check for overall score and movies_guessed
        for line in msg_txt.split('\n'):

            if line.find(trophy_symbol) >= 0:
                game_score = line.split(' ')[1]

            if line.find(check_mark) >= 0:
                movies_guessed += 1

        metric_01 = movies_guessed

    if game_prefix == 'Atlantic':
        game_name = 'atlantic'
        msg_txt = msg_txt.replace('[', '')

        # find position of colon for time, slash for date
        s = msg_txt.find(':')
        d = msg_txt.find('/')
        if s == -1 or d == -1:
            msg_back = [False, 'Invalid format']
            return msg_back

        # find score and date
        game_score = msg_txt[s - 2:s + 3].strip()
        r_month = msg_txt[d - 2:d].strip().zfill(2)
        r_day = msg_txt[d + 1:d + 3].strip().zfill(2)

        # find year (this is generally not working)
        if '202' in msg_txt:
            y = msg_txt.find('202')
            r_year = msg_txt[y:y + 4]
        else:
            r_year = game_date[0:4]

        game_date = f'{r_year}-{r_month}-{r_day}'

    if game_prefix == 'Connections':
        game_name = 'connections'
        
        # split the text by newlines
        lines = msg_txt.strip().split("\n")

        # only keep lines that contain at least one emoji square
        emoji_squares = ["🟨", "🟩", "🟦", "🟪"]
        lines = [line for line in lines if any(emoji in line for emoji in emoji_squares)]

        max_possible_guesses = 7
        guesses_taken = len(lines)
        completed_lines = 0

        # purple square bonus
        metric_01 = 1 if lines[0].count("🟪") == 4 else 0

        for line in lines:
            # a line is considered completed if all emojis are the same
            if len(set(line)) == 1:
                completed_lines += 1

        metric_02 = int(completed_lines == 4) # did the user complete the puzzle?
        game_score = f"{guesses_taken}/{max_possible_guesses}" if metric_02 == 1 else f"X/{max_possible_guesses}"

    if game_prefix == '#Emovi':
        game_name = 'emovi'

        # split the string into lines
        lines = msg_txt.split('\n')

        # find the line with the score
        for line in lines:
            if '🟥' in line or '🟩' in line:
                score_line = line
                break
        else:
            raise ValueError('No score line found in game text')

        # count the total squares and the position of the green square
        total_squares = 3
        green_square_pos = None
        for i, char in enumerate(score_line):
            if char == '🟩':
                green_square_pos = i

        # if no green square was found, the score is 0
        if green_square_pos is None:
            game_score = f"X/{total_squares}"
            metric_02 = 0
        else:
            game_score = f"{green_square_pos+1}/{total_squares}"
            metric_02 = 1

    if game_prefix == 'Daily Crosswordle':
        game_name = 'crosswordle'
        match = re.search(r"(?:(\d+)m\s*)?(\d+)s", msg_txt) # make minutes optional
        metric_02 = 1
        if match:
            minutes = match.group(1)
            seconds = int(match.group(2))
            seconds_str = str(seconds).zfill(2)
            
            # If no minutes are present, consider it as 0
            minutes = 0 if minutes is None else int(minutes)
            game_score = f"{minutes}:{seconds_str}"

    # put into dataframe
    my_cols = ['game_date', 'game_name', 'game_score', 'added_ts', 'discord_id', 'game_dtl', 'metric_01', 'metric_02', 'metric_03']
    my_data = [[game_date, game_name, game_score, added_ts, discord_id, game_dtl, metric_01, metric_02, metric_03]]
    df = pd.DataFrame(data=my_data, columns=my_cols)

    # send to sql using new function
    await send_df_to_sql(df, 'game_history', if_exists='append')

    msg_back = f"Added {game_name} for {discord_id} on {game_date} with score {game_score}"

    return msg_back
