from rest_framework import permissions


class OrganizerPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'organizer'


class ParticipantPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'participant'


class AdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class EventOwnerPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.organizer_id


class TicketHostPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.invoice_id.event_id.organizer_id


class InvoiceOwnerPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.user_id


class ReviewOwnerPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.participant_id


class ResponseOwnerPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.organizer_id


class FavoriteOwnerPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.participant_id

