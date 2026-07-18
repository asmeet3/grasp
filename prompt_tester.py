import os
from anthropic import Anthropic
from dotenv import load_dotenv

# Load variables from a .env file in the current directory (if present)
load_dotenv()

# Initialize the Anthropic client
# Ensure ANTHROPIC_API_KEY is set in your environment variables or .env file
client = Anthropic()

# ---------------------------------------------------------------------------
# Hardcoded system prompt. Edit this string directly to change behavior —
# triple quotes let you write it across multiple lines.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are a query-shortening assistant for an enterprise search and RAG system.

Your task is to convert a user's natural-language question into one or more short, keyword-focused search queries.

Rules:
1. Remove conversational filler, articles, pronouns, question words, and unnecessary grammar.
2. Keep the core subject, entity names, project names, API names, product names, status terms, and important qualifiers.
3. Preserve exact technical identifiers exactly as written, including underscores, hyphens, casing where possible, and special terms such as API names.
4. Do not add information, assumptions, synonyms, or explanations not present in the user query.
5. Prefer concise noun phrases or keyword groups, typically 2 to 6 words.
6. Do not use full sentences, punctuation, or question marks in shortened queries.
7. Return one query when all parts of the question concern the same subject or closely related information.
8. Return multiple queries only when the question covers distinct or mutually exclusive entities, systems, applications, teams, projects, or topics that should be searched independently.
9. For comparisons between separate entities, return one shortened query for each entity. Do not include words such as `difference`, `versus`, or `vs`.
10. If multiple independent questions share relevant context, retain that context in each query where necessary for accurate retrieval.
11. Maintain the original language used by the user.
12. Return only a valid Python list of double-quoted strings. Do not add markdown, explanations, labels, or code fences.

"""

def query_llm(system_prompt, user_query, temperature, model="claude-haiku-4-5-20251001"):
    """Sends the request to the Anthropic API and returns the response."""
    try:
        # Note: Claude requires a max_tokens parameter. 1024 is plenty for keyword extraction.
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=temperature,
            system=system_prompt, # Claude takes the system prompt as a separate top-level parameter
            messages=[
                {"role": "user", "content": user_query}
            ]
        )
        # Claude returns a list of content blocks, we want the text from the first one
        return response.content[0].text.strip()
    except Exception as e:
        return f"API Error: {e}"

def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Please set it using: export ANTHROPIC_API_KEY='your-key-here'")
        return

    print("=" * 60)
    print("🧪 Interactive System Prompt Tester (Claude Haiku 4.5) 🧪")
    print("=" * 60)

    print(f"\nSystem prompt (hardcoded):\n{'-' * 60}\n{SYSTEM_PROMPT}\n{'-' * 60}")

    # Outer loop handles restarting/changing the temperature
    while True:
        print("\n" + "-" * 60)
        print("CONFIGURATION SETUP")
        print("-" * 60)

        # Get Temperature
        while True:
            temp_input = input("Enter TEMPERATURE (e.g., 0.0, 0.3, 0.7) [default: 0.0]: ").strip()
            if not temp_input:
                temperature = 0.0
                break
            try:
                temperature = float(temp_input)
                break
            except ValueError:
                print("Invalid number. Please enter a valid float (e.g., 0.2).")

        print(f"\n✅ Temperature set to {temperature}.")
        print("Type 'restart' to change the temperature.")
        print("Type 'exit' to quit the program.\n")

        # Inner loop handles continuous user queries
        while True:
            user_query = input("👤 User Query: ").strip()

            if not user_query:
                continue

            if user_query.lower() == 'restart':
                print("\n🔄 Restarting configuration setup...\n")
                break # Breaks inner loop, returns to outer loop

            if user_query.lower() == 'exit':
                print("👋 Goodbye!")
                return # Exits the entire script

            # Get response and print
            print("🤖 Assistant: ", end="")
            result = query_llm(SYSTEM_PROMPT, user_query, temperature)
            print(result)
            print("-" * 60)

if __name__ == "__main__":
    main()