import json
from datetime import datetime, timedelta
import tiktoken
from openai import AsyncOpenAI
import os
from pytz import timezone
import pytz
from global_functions import get_now

# chat gpt
gpt_key = os.getenv('OPENAI_API_KEY')
openai_client = AsyncOpenAI(api_key=gpt_key)

# gpt command
async def fetch_gpt_response(ctx, query: str):
    print(f"fetch_gpt_response called with query: {query}")

    try:
        is_allowed_author = ctx.author.id == 340940380927295491 or ctx.author.id == 163849350827606016
    except AttributeError as e:
        print(f"Error checking author ID: {e}")
        return await ctx.send("Error: Unable to verify author permissions.")

    if not is_allowed_author:
        print(f"User {ctx.author.name} is not allowed to use the GPT command.")
        return await ctx.send(f"Sorry, {ctx.author.name}, this feature is locked for now.")
    
    try:
        print(f"User requested GPT response.")
        gpt_model = "gpt-4o-mini" #"gpt-4o" # gpt-3.5-turbo-0125

        # Read the messages.json file
        try:
            with open(f'files/guilds/{ctx.guild.name}/messages.json', 'r') as file:
                messages_data = json.load(file)
        except FileNotFoundError as e:
            print(f"Error reading messages.json file: {e}")
            return await ctx.send("Error: messages.json file not found.")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from messages.json file: {e}")
            return await ctx.send("Error: Failed to decode messages.json file.")

        # Filter messages for the current channel
        try:
            channel_messages = [msg for msg in messages_data.values() if isinstance(msg, dict) and msg.get('channel_id') == ctx.channel.id]
        except KeyError as e:
            print(f"Error filtering messages: {e}")
            return await ctx.send("Error: Invalid message format in messages.json file.")

        # Get the current time in Eastern Time
        eastern_tz = timezone('US/Eastern')
        current_time = datetime.now(eastern_tz)

        # Filter messages from the past hour
        one_hour_ago = current_time - timedelta(hours=1)
        recent_messages = [
            msg for msg in channel_messages
            if isinstance(msg, dict) and
            eastern_tz.localize(datetime.fromisoformat(msg.get('create_ts', ''))) > one_hour_ago
        ]

        # Format the messages for GPT input
        formatted_messages = "\n".join([f"{msg.get('author_nm', '')}: {msg.get('content', '')}" for msg in recent_messages])

        # Calculate the number of tokens
        try:
            encoding = tiktoken.encoding_for_model(gpt_model)
            tokens = encoding.encode(formatted_messages)
        except Exception as e:
            print(f"Error encoding messages: {e}")
            return await ctx.send("Error: Failed to encode messages for GPT input.")
        
        max_tokens = 4096

        # Truncate the messages to fit within the limit
        if len(tokens) > max_tokens:
            truncated_tokens = tokens[:max_tokens]
            formatted_messages = encoding.decode(truncated_tokens)

        # Ask GPT for a summary
        try:
            response = await openai_client.chat.completions.create(
                model=gpt_model,
                messages=[
                    {"role": "system", "content": "Summarize the following conversation:"},
                    {"role": "user", "content": formatted_messages}
                ],
                max_tokens=1000
            )
        except Exception as e:
            print(f"Error fetching GPT response: {e}")
            return await ctx.send("Error: Failed to fetch GPT response.")

        print('sending response now...')
 
        # Sending the response back to the Discord channel
        try:
            await ctx.send(response.choices[0].message.content)
        except Exception as e:
            print(f"Error sending response to Discord: {e}")
            return await ctx.send("Error: Failed to send response to Discord.")

    except Exception as e:
        print(f"Error in fetch_gpt_response: {e}")
        error_message = str(e)
        if "token limit exceeded" in error_message.lower():
            custom_message = "Error: Token limit exceeded. Please try a shorter query."
        else:
            custom_message = f"Error: {error_message}"

        await ctx.send(custom_message)