from agent import Agent


def main():
    
    print("  Prodigal Payment Collection Agent — Interactive CLI")
    print("  Type 'quit' or 'exit' to end the session.")
    print('\n')
    print()

    agent = Agent()

    # start with just a greeting --> 
    opening = agent.next("")
    print(f"Agent: {opening['message']}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("Agent: Thank you. Goodbye!")
            break

        if not user_input:
            continue

        response = agent.next(user_input)
        print(f"\nAgent: {response['message']}\n")


if __name__ == "__main__":
    main()
