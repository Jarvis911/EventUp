import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from .models import Event, FavoriteEvent, UserPreference, Review, Category
from django.db.models import Avg, Max


def generate_category_index_map():
    category_ids = list(Category.objects.filter(active=True).values_list('id', flat=True))
    return {cat_id: idx for idx, cat_id in enumerate(category_ids)}, len(category_ids)


def generate_event_features(event, max_views, category_index_map, category_count):
    category_vector = np.zeros(category_count)

    # Category (one-hot)
    category_index = category_index_map.get(event.category_id_id)
    if category_index is not None:
        category_vector[category_index] = 1

    # Normalized views
    views = event.views / max_views if max_views else 0

    # Average rating (scale 0 - 1)
    rating = float(event.avg_rating or 0) / 5

    # Combine
    return np.concatenate([category_vector, [views, rating]])


def generate_user_profile(user, category_index_map, category_count):
    # Generate user preference vector
    preferred = UserPreference.objects.filter(user=user).values_list('category_id', flat=True)
    favorite = FavoriteEvent.objects.filter(participant_id=user).values_list('event_id__category_id', flat=True)

    category_vector = np.zeros(category_count)
    for cid in set(preferred).union(favorite):
        idx = category_index_map.get(cid)
        if idx is not None:
            category_vector[idx] = 1

    # Rating and favorite
    avg_rating = Review.objects.filter(participant_id=user, active=True).aggregate(avg_rating=Avg('rating'))['r'] or 0
    favor_score = min(FavoriteEvent.objects.filter(participant_id=user).count() / 10, 1.0)

    return np.concatenate([category_vector, [favor_score, avg_rating / 5]])


def recommend_events(user, limit=10):
    category_index_map, category_count = generate_category_index_map()
    user_vector = generate_user_profile(user, category_index_map, category_count)

    events_qs = Event.objects.filter(active=True).select_related('category_id') \
        .annotate(avg_rating=Avg('review__rating'))

    max_views = events_qs.aggregate(max=Max('views'))['max'] or 1
    favorite_event_ids = set(FavoriteEvent.objects.filter(participant_id=user).values_list('event_id', flat=True))
    candidate_events = []
    vectors = []

    for event in events_qs:
        if event.id not in favorite_event_ids:
            vec = generate_event_features(event, max_views, category_index_map, category_count)
            vectors.append(vec)
            candidate_events.append(event)

    if not vectors:
        return Event.objects.filter(active=True).order_by('-views')[:limit]

    similarities = cosine_similarity([user_vector], vectors)[0]
    top_indices = np.argsort(similarities)[::-1][:limit]
    top_events = [candidate_events[i] for i in top_indices]

    return top_events


