package pieces 
{
    import org.flixel.FlxG;
    
    public class S extends PuzzlePiece 
    {
        public function S() 
        {
            super("S", 2);
        }
        
        override public function get emitting():Boolean 
        {
            return FlxG.keys.S;
        }
        
        override public function set emitting(val:Boolean):void 
        {
        }
    }
}
