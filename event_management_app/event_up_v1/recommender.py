import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from .models import Event, FavoriteEvent, UserPreference, Review, Category
from django.db.models import Avg, Max


def generate_event_features(event):
    # Generate feature vector for event
    # Feature: category (one-hot), normalized views, average rating
    categories = Category.objects.filter(active=True)
    category_count = categories.count()
    category_vector = np.zeros(category_count)

    # Category (one-hot)
    category_index = list(categories.values_list('id', flat=True)).index(event.category_id.id)
    category_vector[category_index] = 1

    # Normalized views
    max_views = Event.objects.filter(active=True).aggregate(max_views=Max('views'))['max_views'] or 1
    views = event.views / max_views if event.views else 0

    # Average rating (scale 0 - 1)
    avg_rating = Review.objects.filter(event_id=event, active=True).aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
    rating = float(avg_rating) / 5

    # Combine
    feature_vector = np.concatenate([category_vector, [views, rating]])
    return feature_vector


def generate_user_profile(user):
    # Generate user preference vector
    preferred_categories = UserPreference.objects.filter(user=user).values_list('category_id', flat=True)
    favorite_categories = FavoriteEvent.objects.filter(participant_id=user).values_list('event_id__category_id', flat=True)
    category_ids = set(preferred_categories).union(favorite_categories)

    # Create category vector
    categories = Category.objects.filter(active=True)
    category_count = categories.count()
    category_vector = np.zeros(category_count)

    for category_id in category_ids:
        category_index = list(categories.values_list('id', flat=True)).index(category_id)
        category_vector[category_index] = 1

    # Rating and favorite
    avg_rating = Review.objects.filter(participant_id=user, active=True).aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
    rating = avg_rating / 5 if avg_rating else 0

    favor = FavoriteEvent.objects.filter(participant_id=user).count() / 10
    favor = min(favor, 1.0)

    user_vector = np.concatenate([category_vector, [favor, rating]])
    return user_vector


def recommend_events(user, limit=10):
    # Using cosine similarity
    user_vector = generate_user_profile(user)
    events = Event.objects.filter(active=True)

    event_vectors = []
    event_ids = []
    for event in events:
        if not FavoriteEvent.objects.filter(participant_id=user, event_id=event).exists():
            vector = generate_event_features(event)
            event.set_feature_vector(vector)
            event_vectors.append(vector)
            event_ids.append(event.id)

    if not event_vectors:
        return Event.objects.filter(active=True).order_by('-views')[:limit]

    # Calculate similarity
    event_vectors = np.array(event_vectors)
    similarities = cosine_similarity([user_vector], event_vectors)[0]

    # Sort by similarity
    sorted_indices = np.argsort(similarities)[::-1][:limit]
    recommend_event_ids = [event_ids[i] for i in sorted_indices]

    return Event.objects.filter(id__in=recommend_event_ids).order_by('-views')[:limit]