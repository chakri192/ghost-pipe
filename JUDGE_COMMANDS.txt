=== GHOST-PIPE: JUDGE TESTING GUIDE ===

Welcome! If you are evaluating Ghost-Pipe inside a Codespace or local terminal, 
you can copy and paste the exact commands below to test the forensics engine.

(Note: You can also just type `./ghost-pipe.py` to see the help menu).

1. Audit the Zero-Dependency Architecture
-----------------------------------------
./ghost-pipe.py audit


2. Test the Deterministic Engine (Node Port Conflict)
-----------------------------------------
./ghost-pipe.py run -- bash -c "echo 'Error: listen EADDRINUSE: address already in use :::8080'; exit 1"
./ghost-pipe.py diagnose latest


3. Test the Mach-O Binary Parser (Automated Demo)
-----------------------------------------
./ghost-pipe.py demo


4. Test the In-Memory Context Firewall (AWS Key Leak)
-----------------------------------------
./ghost-pipe.py run -- bash -c "echo 'AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE'; exit 1"
./ghost-pipe.py inspect latest
# Notice how the AWS key is stripped out before any AI analysis occurs!


5. Test the Interactive Curses UI (History Ledger)
-----------------------------------------
./ghost-pipe.py board
# Use your UP and DOWN arrow keys to scroll, and 'q' to quit.


6. Run the Internal Self-Test Suite
-----------------------------------------
./ghost-pipe.py self-test
