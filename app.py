import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, g
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
# IMPORTANT: When deploying on Render, set this ADMIN_PASSWORD in your environment variables.
# The default is 'secret' for local development if the environment variable is not found.
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
    # The note will be stored here. We will only use the entry with id=1.
    admin_note = db.Column(db.Text, default="") 

# --- Elo Calculation Logic ---
def calculate_elo(winner_elo, loser_elo):
    """Calculates the new Elo ratings for the winner and loser, rounding results."""
    K = 32 # The maximum change in a rating from one game
    
    # Calculate expected outcomes (Ea)
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    
    # Calculate new Elo (Ra' = Ra + K * (S - Ea))
    new_winner_elo_float = winner_elo + K * (1 - expected_winner)
    new_loser_elo_float = loser_elo + K * (0 - expected_loser)
    
    # Round to the nearest integer for the final Elo score
    new_winner_elo = int(round(new_winner_elo_float))
    new_loser_elo = int(round(new_loser_elo_float))
    
    return new_winner_elo, new_loser_elo

# --- Routes ---
@app.route('/')
def index():
    # 1. Get Leaderboard sorted by Elo
    players = Player.query.order_by(Player.elo.desc()).all()
    
    # 2. Get Match History (last 5 games) - Eagerly load players
    matches = Match.query.options(
        joinedload(Match.winner), 
        joinedload(Match.loser)
    ).order_by(Match.date.desc()).limit(5).all()
    
    # 3. Get the Admin Note
    config = LeagueConfig.query.get(1)
    admin_note = config.admin_note if config else ""
    
    def format_date(dt_object):
        return dt_object.strftime('%b %d, %H:%M')

    return render_template('index.html', 
                           players=players, 
                           matches=matches,
                           admin_note=admin_note,
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
    
    # 2. Calculate new Elo (Post-match Elos)
    winner_post_elo, loser_post_elo = calculate_elo(winner_pre_elo, loser_pre_elo)
    
    # 3. Update Players (Elo + Win/Loss counts)
    winner.elo = winner_post_elo
    winner.wins += 1
    loser.elo = loser_post_elo
    loser.losses += 1
    
    # 4. Record the Match with pre and post Elos
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
    """Removes a player and all associated matches, only if password is correct."""
    # Check password first
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401

    player = db.session.get(Player, player_id)
    if player:
        # 1. Delete associated matches first
        Match.query.filter((Match.winner_id == player_id) | (Match.loser_id == player_id)).delete(synchronize_session='fetch')
        
        # 2. Delete the player
        db.session.delete(player)
        db.session.commit()
    return redirect('/')

@app.route('/remove_match/<int:match_id>', methods=['POST'])
def remove_match(match_id):
    """Removes a match and reverts the Elo, wins, and losses of both players, only if password is correct."""
    # Check password first
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401
        
    match = db.session.get(Match, match_id)
    
    if match:
        # 1. Calculate Elo change
        winner_elo_change = match.winner_post_elo - match.winner_pre_elo
        loser_elo_change = match.loser_post_elo - match.loser_pre_elo
        
        # 2. Revert Elo and Win/Loss counts for the match participants
        winner = db.session.get(Player, match.winner_id)
        loser = db.session.get(Player, match.loser_id)

        if winner and loser:
            winner.elo -= winner_elo_change
            winner.wins -= 1
            loser.elo -= loser_elo_change
            loser.losses -= 1

        # 3. Delete the match
        db.session.delete(match)
        
        db.session.commit()
    return redirect('/')
    
@app.route('/update_admin_note', methods=['POST'])
def update_admin_note():
    """Updates the admin note displayed on the front page."""
    if not check_admin_password(request.form.get('admin_password')):
        return "Unauthorized: Incorrect admin password.", 401

    new_note = request.form.get('admin_note', '').strip()
    
    # Get the single config row (we initialized it to id=1)
    config = db.session.get(LeagueConfig, 1)
    
    if config:
        config.admin_note = new_note
        db.session.commit()
        
    return redirect('/')

# --- END ADMIN FEATURES ---

def init_db():
    """Initializes the database structure and ensures a LeagueConfig entry exists."""
    with app.app_context():
        db.create_all()
        # Ensure the LeagueConfig row exists for the admin note
        if LeagueConfig.query.get(1) is None:
            config = LeagueConfig(id=1, admin_note="")
            db.session.add(config)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=80, debug=True)