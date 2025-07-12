"""Layout management for MentraOS UI displays."""

from typing import Optional, Dict, Any, TYPE_CHECKING

from ..protocol.messages import DisplayEventMessage, TextWallLayout
from ..utils.logger import get_logger
from .layout_types import ViewType, LayoutType

if TYPE_CHECKING:
    from ..core.app_session import AppSession

logger = get_logger("layout_manager")


class LayoutManager:
    """Manages UI layouts for MentraOS displays."""
    
    def __init__(self, session: "AppSession"):
        self._session = session
        self._current_layouts: Dict[str, Any] = {}
    
    async def show_text_wall(
        self,
        text: str,
        view: ViewType = ViewType.MAIN,
        duration_ms: Optional[int] = None
    ) -> None:
        """Display text on the glasses using TextWall layout.
        
        Args:
            text: The text to display
            view: Which view to display on (MAIN or OVERLAY)
            duration_ms: How long to display the text (None for permanent)
        """
        layout = TextWallLayout(text=text)
        
        message = DisplayEventMessage(
            sessionId=self._session.session_id,
            packageName=self._session.package_name,
            view=view.value,
            layout=layout,
            durationMs=duration_ms
        )
        
        await self._session.send(message.to_dict())
        
        # Track current layout
        self._current_layouts[view.value] = {
            "type": LayoutType.TEXT_WALL,
            "text": text,
            "duration_ms": duration_ms
        }
        
        logger.info(f"Displayed text wall: '{text}' on {view.value} view")
    
    async def clear(self, view: ViewType = ViewType.MAIN) -> None:
        """Clear the display on the specified view."""
        await self.show_text_wall("", view=view, duration_ms=1)
        
        # Remove from tracking
        self._current_layouts.pop(view.value, None)
        
        logger.info(f"Cleared {view.value} view")
    
    async def show_notification(
        self,
        title: str,
        message: str,
        duration_ms: int = 3000
    ) -> None:
        """Show a notification (uses overlay view).
        
        Args:
            title: Notification title
            message: Notification message
            duration_ms: How long to show notification
        """
        # For now, use text wall on overlay
        # In future, this could use a dedicated notification layout
        notification_text = f"{title}\n{message}" if title else message
        await self.show_text_wall(
            notification_text,
            view=ViewType.OVERLAY,
            duration_ms=duration_ms
        )
    
    def get_current_layout(self, view: ViewType = ViewType.MAIN) -> Optional[Dict[str, Any]]:
        """Get the current layout for a view."""
        return self._current_layouts.get(view.value)
    
    async def refresh(self) -> None:
        """Refresh all current layouts (useful after reconnection)."""
        for view, layout_info in list(self._current_layouts.items()):
            if layout_info["type"] == LayoutType.TEXT_WALL:
                await self.show_text_wall(
                    layout_info["text"],
                    view=ViewType(view),
                    duration_ms=layout_info.get("duration_ms")
                )