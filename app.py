import os
import random
import math
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from dotenv import load_dotenv

# Load environment variables (like DATABASE_URL) from a .env file locally
load_dotenv() 

# --- Application Setup ---
app = Flask(__name__)

# Configuration for SQLAlchemy (PostgreSQL/MySQL)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///pong_league.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- SECURITY CONFIGURATION ---
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'secret')

def check_admin_password(submitted_password):
    """Checks the submitted password against the environment secret."""
    return submitted_password == ADMIN_PASSWORD
# --- END SECURITY CONFIGURATION ---


# --- Database Models ---

class Player(db.Model):
    __tablename__ = 'players'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    elo = db.Column(db.Integer, default=1200)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    
class Match(db.Model):
    __tablename__ = 'matches'
    id = db.Column(db.Integer, primary_key=True)
    winner_id = db.Column(db.Integer, db.ForeignKey('players.id'))
    loser_id = db.Column(db.Integer, db.ForeignKey('players.id'))
    score = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    winner_pre_elo = db.Column(db.Integer)
    loser_pre_elo = db.Column(db.Integer)
    winner_post_elo = db.Column(db.Integer)
    loser_post_elo = db.Column(db.Integer)

    winner = db.relationship("Player", foreign_keys=[winner_id], backref="won_matches")
    loser = db.relationship("Player", foreign_keys=[loser_id], backref="lost_matches")

class LeagueConfig(db.Model):
    __tablename__ = 'league_config'
    id = db.Column(db.Integer, primary_key=True)
    admin_note = db.Column(db.Text, default="")
    # 0: No Tournament | 1: Signup Active | 2: Tournament Active | 3: Tournament Concluded
    tournament_state = db.Column(db.Integer, default=0) 
    
class Tournament(db.Model):
    __tablename__ = 'tournaments'
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date)
    winner_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    final_rankings = db.Column(db.Text, default="{}") 
    
    winner = db.relationship("Player", foreign_keys=[winner_id]) # ADDED RELATIONSHIP

class TournamentSignup(db.Model):
    __tablename__ = 'tournament_signups'
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), unique=True)
    player = db.relationship("Player")

class TournamentMatch(db.Model):
    __tablename__ = 'tournament_matches'
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournaments.id'))
    round_num = db.Column(db.Integer, nullable=False) # 1, 2, 3...
    match_num = db.Column(db.Integer, nullable=False) # 1, 2, 3... within the round
    player1_id = db.Column(db.Integer, db.ForeignKey('players.id'))
    player2_id = db.Column(db.Integer, db.ForeignKey('players.id'))
    winner_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    score = db.Column(db.Text) # e.g., "2-1"
    match_date = db.Column(db.Date, default=date.today)
    best_of_games = db.Column(db.Integer, default=3) # 3 for early rounds, 5 for final

    player1 = db.relationship("Player", foreign_keys=[player1_id])
    player2 = db.relationship("Player", foreign_keys=[player2_id])
    winner = db.relationship("Player", foreign_keys=[winner_id]) # Added winner relationship
    tournament = db.relationship("Tournament", backref="matches")


# --- Utility Functions ---

def get_next_power_of_two(n):
    """Returns the smallest power of two greater than or equal to n."""
    if n <= 0: return 0
    return 2**(n - 1).bit_length()

def generate_bracket(players):
    """
    Generates a single-elimination bracket structure.
    Players are randomly shuffled. Byes are handled if player count is not a power of 2.
    Returns a list of TournamentMatch objects for Round 1.
    """
    random.shuffle(players)
    N = len(players)
    
    # Calculate required bracket size (next power of 2)
    bracket_size = get_next_power_of_two(N)
    byes = bracket_size - N
    
    match_list = []
    
    # Insert Byes (players automatically advance)
    if byes > 0:
        # Simple method: the first 'byes' players get a bye
        players_with_byes = players[:byes]
        players_to_play = players[byes:]
        
        # Matches for players who get a bye (these automatically advance to Round 2)
        for i, player in enumerate(players_with_byes):
            match_list.append(TournamentMatch(
                round_num=1,
                match_num=i + 1,
                player1_id=player.id,
                player2_id=None, # Indicates a bye
                winner_id=player.id,
                score="BYE",
                best_of_games=3 
            ))
            
        # Matches for players who play in Round 1
        num_r1_matches = (N - byes) // 2
        for i in range(num_r1_matches):
            p1 = players_to_play[i * 2]
            p2 = players_to_play[i * 2 + 1]
            match_list.append(TournamentMatch(
                round_num=1,
                match_num=byes + i + 1,
                player1_id=p1.id,
                player2_id=p2.id,
                best_of_games=3
            ))
            
    else:
        # If N is power of 2, all players play
        for i in range(N // 2):
            p1 = players[i * 2]
            p2 = players[i * 2 + 1]
            match_list.append(TournamentMatch(
                round_num=1,
                match_num=i + 1,
                player1_id=p1.id,
                player2_id=p2.id,
                best_of_games=3
            ))
            
    return match_list


def calculate_elo(winner_elo, loser_elo):
    """Calculates the new Elo ratings for the winner and loser, rounding results."""
    K = 32
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    new_winner_elo_float = winner_elo + K * (1 - expected_winner)
    new_loser_elo_float = loser_elo + K * (0 - expected_loser)
    new_winner_elo = int(round(new_winner_elo_float))
    new_loser_elo = int(round(new_loser_elo_float))
    return new_winner_elo, new_loser_elo

# --- Routes ---
@app.route('/')
def index():
    all_players = Player.query.order_by(Player.elo.desc()).all()
    
    # Separate players into active (W+L > 0) and inactive (W+L = 0)
    active_players = [p for p in all_players if p.wins + p.losses > 0]
    inactive_players = [p for p in all_players if p.wins + p.losses == 0]

    matches = Match.query.options(
        joinedload(Match.winner), 
        joinedload(Match.loser)
    ).order_by(Match.date.desc()).limit(10).all()
    
    config = LeagueConfig.query.get(1)
    admin_note = config.admin_note if config else ""
    tournament_state = config.tournament_state if config else 0
    
    current_tournament = None
    signed_up_players = []
    current_matches = [] # Will now hold ALL tournament matches
    concluded_tournament = None
    max_round_num = 0 # Track the current/max round number
    
    if tournament_state == 1: # Signup Active
        current_tournament = Tournament.query.order_by(Tournament.id.desc()).first()
        signed_up_players = [s.player for s in TournamentSignup.query.options(joinedload(TournamentSignup.player)).all()]
        
    elif tournament_state == 2: # Tournament Active
        current_tournament = Tournament.query.order_by(Tournament.id.desc()).first()
        if current_tournament:
            # Load ALL matches for the current tournament, ordered by round and match number
            current_matches = TournamentMatch.query.options(
                joinedload(TournamentMatch.player1), 
                joinedload(TournamentMatch.player2),
                joinedload(TournamentMatch.winner) # Load the winner for completed matches
            ).filter(
                TournamentMatch.tournament_id == current_tournament.id
            ).order_by(TournamentMatch.round_num, TournamentMatch.match_num).all()
            
            # Determine the maximum round number
            if current_matches:
                 max_round_num = max(m.round_num for m in current_matches)
            
    elif tournament_state == 3: # Tournament Concluded
        # Get the last concluded tournament and load its winner
        concluded_tournament = Tournament.query.options(joinedload(Tournament.winner)).order_by(Tournament.id.desc()).first()

        # Fetch all matches for the concluded tournament
        if concluded_tournament:
            current_matches = TournamentMatch.query.options(
                joinedload(TournamentMatch.player1), 
                joinedload(TournamentMatch.player2),
                joinedload(TournamentMatch.winner) 
            ).filter(
                TournamentMatch.tournament_id == concluded_tournament.id
            ).order_by(TournamentMatch.round_num, TournamentMatch.match_num).all()
            
            if current_matches:
                 max_round_num = max(m.round_num for m in current_matches)

    def format_date(dt_object):
        return dt_object.strftime('%b %d, %H:%M')

    return render_template('index.html', 
                           players=active_players, # PASSING active_players as 'players' for compatibility
                           inactive_players=inactive_players, # NEW: Passing inactive list
                           matches=matches,
                           admin_note=admin_note,
                           tournament_state=tournament_state,
                           signed_up_players=signed_up_players,
                           all_tournament_matches=current_matches, # Changed variable name
                           max_round_num=max_round_num,           # New variable
                           concluded_tournament=concluded_tournament,
                           format_date=format_date)

@app.route('/add_player', methods=['POST'])
def add_player():
    name = request.form['name']
    if name:
        new_player = Player(name=name)
        db.session.add(new_player)
        db.session.commit()
    return redirect('/')

@app.route('/log_match', methods=['POST'])
def log_match():
    # ... (log_match logic remains the same) ...
    winner_id = int(request.form['winner'])
    loser_id = int(request.form['loser'])
    score = request.form['score']
    
    if winner_id == loser_id:
        return redirect('/') 

    winner = db.session.get(Player, winner_id)
    loser = db.session.get(Player, loser_id)
    
    if not winner or not loser:
        return "One or both players not found.", 404
        
    winner_pre_elo = winner.elo
    loser_pre_elo = loser.elo
    
    winner_post_elo, loser_post_elo = calculate_elo(winner_pre_elo, loser_pre_elo)
    
    winner.elo = winner_post_elo
    winner.wins += 1
    loser.elo = loser_post_elo
    loser.losses += 1
    
    new_match = Match(
        winner_id=winner_id,
        loser_id=loser_id,
        score=score,
        winner_pre_elo=winner_pre_elo,
        loser_pre_elo=loser_pre_elo,
        winner_post_elo=winner_post_elo,
        loser_post_elo=loser_post_elo
    )
    db.session.add(new_match)
    
    db.session.commit()
    return redirect('/')

# --- ADMIN FEATURES ---

@app.route('/check_admin_password', methods=['POST'])
def check_admin_password_route():
    """Endpoint to validate the admin password via AJAX/Fetch."""
    submitted_password = request.json.get('admin_password')
    if check_admin_password(submitted_password):
        return {"success": True}, 200
    else:
        return {"success": False, "message": "Incorrect Password"}, 401 

@app.route('/remove_player/<int:player_id>', methods=['POST'])
def remove_player(player_id):
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    player = db.session.get(Player, player_id)
    if player:
        Match.query.filter((Match.winner_id == player_id) | (Match.loser_id == player_id)).delete(synchronize_session='fetch')
        db.session.delete(player)
        db.session.commit()
    return redirect('/')

@app.route('/remove_match/<int:match_id>', methods=['POST'])
def remove_match(match_id):
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    match = db.session.get(Match, match_id)
    if match:
        winner_elo_change = match.winner_post_elo - match.winner_pre_elo
        loser_elo_change = match.loser_post_elo - match.loser_pre_elo
        winner = db.session.get(Player, match.winner_id)
        loser = db.session.get(Player, match.loser_id)
        if winner and loser:
            winner.elo -= winner_elo_change
            winner.wins -= 1
            loser.elo -= loser_elo_change
            loser.losses -= 1
        db.session.delete(match)
        db.session.commit()
    return redirect('/')
    
@app.route('/update_admin_note', methods=['POST'])
def update_admin_note():
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    new_note = request.form.get('admin_note', '').strip()
    config = db.session.get(LeagueConfig, 1)
    if config:
        config.admin_note = new_note
        db.session.commit()
    return redirect('/')

# --- NEW TOURNAMENT ROUTES ---

@app.route('/admin_start_signup', methods=['POST'])
def admin_start_signup():
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    
    config = db.session.get(LeagueConfig, 1)
    if config.tournament_state == 0 or config.tournament_state == 3:
        # Clear old signups and create a new Tournament entry
        TournamentSignup.query.delete()
        
        # Also clear old tournament matches before starting a new one
        TournamentMatch.query.delete() 
        db.session.commit() 
        
        new_tournament = Tournament(start_date=date.today())
        db.session.add(new_tournament)
        config.tournament_state = 1 # Set to Signup Active
        db.session.commit()
    return redirect('/')

@app.route('/admin_start_tournament', methods=['POST'])
def admin_start_tournament():
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
        
    config = db.session.get(LeagueConfig, 1)
    if config.tournament_state != 1:
        return "Signup is not currently active.", 400
        
    signed_up_players = [s.player for s in TournamentSignup.query.options(joinedload(TournamentSignup.player)).all()]
    if len(signed_up_players) < 2:
        return "Need at least 2 players to start a tournament.", 400
        
    current_tournament = Tournament.query.order_by(Tournament.id.desc()).first()
    if not current_tournament:
        return "No active tournament structure found.", 404
        
    # Generate initial bracket
    initial_matches = generate_bracket(signed_up_players)
    
    for match in initial_matches:
        match.tournament_id = current_tournament.id
        db.session.add(match)

    config.tournament_state = 2 # Set to Tournament Active
    db.session.commit()
    return redirect('/')
    
@app.route('/admin_end_tournament', methods=['POST'])
def admin_end_tournament():
    # In a real app, this is where you'd calculate final rankings.
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    
    config = db.session.get(LeagueConfig, 1)
    config.tournament_state = 0 # Concluded
    db.session.commit()
    return redirect('/')

@app.route('/admin_start_next_round', methods=['POST'])
def admin_start_next_round():
    """Processes winners of the current round and generates matches for the next round."""
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
    
    config = db.session.get(LeagueConfig, 1)
    if config.tournament_state != 2:
        return "Tournament is not currently active.", 400
        
    current_tournament = Tournament.query.order_by(Tournament.id.desc()).first()
    if not current_tournament:
        return "No active tournament found.", 404
        
    # 1. Find the current highest round number in the tournament
    current_round = db.session.query(db.func.max(TournamentMatch.round_num)).filter(
        TournamentMatch.tournament_id == current_tournament.id
    ).scalar()
    
    if current_round is None:
        return "No matches generated for this tournament.", 404

    # 2. Check if all matches in the current round are completed (excluding those with a BYE)
    incomplete_matches = TournamentMatch.query.filter(
        TournamentMatch.tournament_id == current_tournament.id,
        TournamentMatch.round_num == current_round,
        TournamentMatch.winner_id == None,
        TournamentMatch.player2_id != None # Exclude BYE matches which are auto-completed
    ).all()

    if incomplete_matches:
        # If matches are incomplete, do not advance the round
        return redirect(url_for('index')) 

    # 3. All matches are complete. Find the winners of the current round.
    current_round_winners = TournamentMatch.query.filter(
        TournamentMatch.tournament_id == current_tournament.id,
        TournamentMatch.round_num == current_round,
        TournamentMatch.winner_id != None # Only completed matches (including BYEs)
    ).order_by(TournamentMatch.match_num).all()
    
    # 4. Check for tournament winner (Only one winner remains)
    winners_ids = [match.winner_id for match in current_round_winners]
    
    if len(winners_ids) <= 1:
        # The single winner is the champion
        winner_id = winners_ids[0] if winners_ids else None
        
        current_tournament.winner_id = winner_id
        current_tournament.end_date = date.today()
        config.tournament_state = 3 # Concluded
        db.session.commit()
        return redirect('/')

    # 5. Generate the next round's matches
    next_round = current_round + 1
    
    # Ensure number of winners is even for match generation, otherwise add a BYE (not implemented fully here for simplicity)
    # The `generate_bracket` function handles initial byes, but mid-tournament byes are not typically needed for an even number of players advancing.
    
    num_matches = len(winners_ids) // 2
    
    # Check if there is an odd number of winners. If so, the last winner gets a bye.
    last_winner_gets_bye = (len(winners_ids) % 2 != 0)
    
    for i in range(num_matches):
        p1_id = winners_ids[i * 2]
        p2_id = winners_ids[i * 2 + 1]
        
        # Check if this is the final match (i.e., only one match left in this round)
        is_final_round = (num_matches + (1 if last_winner_gets_bye else 0) == 1) and next_round > 1
        best_of = 5 if is_final_round else 3
        
        new_match = TournamentMatch(
            tournament_id=current_tournament.id,
            round_num=next_round,
            match_num=i + 1,
            player1_id=p1_id,
            player2_id=p2_id,
            best_of_games=best_of
        )
        db.session.add(new_match)

    # Handle a potential mid-tournament bye if the number of winners is odd
    if last_winner_gets_bye:
        bye_player_id = winners_ids[-1]
        
        new_match = TournamentMatch(
            tournament_id=current_tournament.id,
            round_num=next_round,
            match_num=num_matches + 1,
            player1_id=bye_player_id,
            player2_id=None,
            winner_id=bye_player_id,
            score="BYE",
            best_of_games=3 
        )
        db.session.add(new_match)


    db.session.commit()
    return redirect('/')

@app.route('/signup_for_tournament', methods=['POST'])
def signup_for_tournament():
    """Adds the player selected in the form to the tournament signup list."""
    
    # 1. Get the player_id from the form data
    try:
        player_id = int(request.form['player_id'])
    except:
        return "Invalid player ID submitted.", 400

    config = db.session.get(LeagueConfig, 1)
    if config.tournament_state != 1:
        return "Signup is not currently active.", 400
        
    existing_signup = TournamentSignup.query.filter_by(player_id=player_id).first()
    if existing_signup:
        # Already signed up, just redirect
        return redirect('/') 
        
    new_signup = TournamentSignup(player_id=player_id)
    db.session.add(new_signup)
    db.session.commit()
    return redirect('/')

# --- Tournament Match Logging (Simplified) ---
@app.route('/log_tournament_match/<int:tmatch_id>', methods=['POST'])
def log_tournament_match(tmatch_id):
    """Logs the result of a tournament match."""
    tmatch = db.session.get(TournamentMatch, tmatch_id)
    
    if not tmatch or tmatch.winner_id is not None:
        return "Match not found or already completed.", 404

    winner_id = int(request.form['winner_id'])
    score = request.form['score']
    
    # Determine winner and loser IDs
    if winner_id == tmatch.player1_id:
        if tmatch.player2_id is None:
             return "Cannot log a winner for a BYE match.", 400
        # The winner is player1
    elif winner_id == tmatch.player2_id:
        # The winner is player2
        pass
    else:
        return "Invalid winner submitted.", 400
        
    # Log the match result
    tmatch.winner_id = winner_id
    tmatch.score = score
    tmatch.match_date = date.today()
        
    # Logic for generating the next round is now in admin_start_next_round
    
    db.session.commit()
    return redirect('/')


def init_db():
    """Initializes the database structure and ensures a LeagueConfig entry exists."""
    with app.app_context():
        db.create_all()
        # Ensure the LeagueConfig row exists for the admin note
        if LeagueConfig.query.get(1) is None:
            config = LeagueConfig(id=1, admin_note="", tournament_state=0)
            db.session.add(config)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=80, debug=True)