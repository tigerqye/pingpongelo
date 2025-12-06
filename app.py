import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload # <-- New Import
from dotenv import load_dotenv

# Load environment variables (like DATABASE_URL) from a .env file locally
load_dotenv() 

# --- Application Setup ---
app = Flask(__name__)

# Configuration for SQLAlchemy (PostgreSQL/MySQL)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///pong_league.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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

    # Relationships defined here allow access via match.winner and match.loser
    winner = db.relationship("Player", foreign_keys=[winner_id], backref="won_matches")
    loser = db.relationship("Player", foreign_keys=[loser_id], backref="lost_matches")

# --- Elo Calculation Logic ---
def calculate_elo(winner_elo, loser_elo):
    K = 32
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    
    new_winner_elo = winner_elo + K * (1 - expected_winner)
    new_loser_elo = loser_elo + K * (0 - expected_loser)
    
    return int(new_winner_elo), int(new_loser_elo)

# --- Routes ---
@app.route('/')
def index():
    # 1. Get Leaderboard sorted by Elo
    players = Player.query.order_by(Player.elo.desc()).all()
    
    # 2. Get Match History (last 5 games)
    # FIX: Use joinedload to eagerly fetch the winner and loser relationships
    # This prevents the 'lazy loading' issue in production environments.
    matches = Match.query.options(
        joinedload(Match.winner), 
        joinedload(Match.loser)
    ).order_by(Match.date.desc()).limit(5).all()
    
    # Helper function to format the timestamp for the template
    def format_date(dt_object):
        return dt_object.strftime('%b %d, %H:%M')

    return render_template('index.html', 
                           players=players, 
                           matches=matches,
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

    # 1. Get current Player objects
    winner = db.session.get(Player, winner_id) # Use db.session.get for primary key lookup
    loser = db.session.get(Player, loser_id)
    
    if not winner or not loser:
        # It's better to return a useful error if a player ID is invalid
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

def init_db():
    """Initializes the database structure if it doesn't exist."""
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=80, debug=True)