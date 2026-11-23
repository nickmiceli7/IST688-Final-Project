# Abhishek Hundalekar: Part D (Host: Conversation and Memory)

**Status as of Nov 22, 2026:** working with sample data. Code: `app.py`.

## Completed
- Intake form: party size, day and time, mode, budget, cuisine, diet
- Chat refinement: follow-ups such as "cheaper" or "pickup" reuse the stored request
- Short-term memory: recent messages plus a running summary of the request
- Sign-in with name and passcode, plus a consent checkbox
- Saved profile loaded on sign-in; view and delete buttons in the sidebar
- Final answer format: restaurants with dish, price, hours, data date, action link, allergy note
- Tested in GitHub Codespaces on Nov 22

## Not done yet (planned by Dec 6)
- Replace sample results with Dominick's `find_restaurants` (B) and Ashish's `check_candidates` (C)
- Move profiles from a local JSON file to Supabase
- Replace keyword rules with an OpenAI/LangChain chat call
- Follow-up question about the last choice
- Report link on each result

## Changes from the Nov 15 plan
- Built with sample data so Part D does not wait on B and C.
- Profiles are stored in a local file for now. Supabase is planned.

## Needs from teammates
- Input and output format of `find_restaurants` and `check_candidates`
